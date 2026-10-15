"""Connecteur Gemini (API generativelanguage, `generateContent`).

Particularités gérées :
- rôles `user` / `model` ; le message système est `systemInstruction` ;
- les outils sont des `functionDeclarations` dont le schéma est un
  sous-ensemble d'OpenAPI : les mots-clés non pris en charge sont retirés ;
- les appels n'ont pas d'identifiant : des identifiants locaux sont créés ;
- les parties `functionCall` brutes (qui peuvent porter une signature de
  raisonnement) sont rejouées telles quelles au tour suivant ;
- la clé passe par l'en-tête `x-goog-api-key`, jamais dans l'URL
  (qui pourrait finir dans des journaux).
"""

from __future__ import annotations

import copy
from typing import Any

from .base import AssistantTurn, ChatProvider, Message, ToolCall, ToolSpec
from ..errors import ProviderError

_ALLOWED_SCHEMA_KEYS = {
    "type", "format", "description", "nullable", "enum", "properties",
    "required", "items", "minimum", "maximum", "minItems", "maxItems",
}


def sanitize_schema(schema: Any) -> Any:
    """Réduit un JSON Schema au sous-ensemble accepté par Gemini."""
    if isinstance(schema, list):
        return [sanitize_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    schema = copy.deepcopy(schema)

    # `anyOf: [{type: X}, {type: null}]` (Optional pydantic) => type X nullable.
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        schema.pop("anyOf")
        if options:
            schema.update(options[0])
            schema["nullable"] = True

    cleaned: dict = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {k: sanitize_schema(v) for k, v in value.items()}
        elif key == "items":
            cleaned[key] = sanitize_schema(value)
        else:
            cleaned[key] = value
    return cleaned


class GeminiProvider(ChatProvider):
    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.settings.api_key or "",
            "content-type": "application/json",
        }

    def _model_path(self) -> str:
        model = self.settings.model
        return model if model.startswith("models/") else f"models/{model}"

    @staticmethod
    def _encode(messages: list[Message]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        contents: list[dict] = []

        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)

            elif message.role == "user":
                contents.append({"role": "user", "parts": [{"text": message.content}]})

            elif message.role == "assistant":
                if message.provider_data:
                    parts = message.provider_data
                else:
                    parts = []
                    if message.content:
                        parts.append({"text": message.content})
                    for call in message.tool_calls:
                        parts.append(
                            {"functionCall": {"name": call.name, "args": call.arguments}}
                        )
                contents.append({"role": "model", "parts": parts})

            elif message.role == "tool":
                part = {
                    "functionResponse": {
                        "name": message.name or "",
                        "response": {"result": message.content},
                    }
                }
                last = contents[-1] if contents else None
                if (
                    last
                    and last["role"] == "user"
                    and last["parts"]
                    and "functionResponse" in last["parts"][0]
                ):
                    last["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})

        return "\n\n".join(system_parts), contents

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> dict:
        system, contents = self._encode(messages)
        advanced = self.settings.advanced

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": advanced.temperature,
                "maxOutputTokens": advanced.max_output_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": sanitize_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
            if not allow_tools:
                body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        return body

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> AssistantTurn:
        url = f"{self.settings.base_url}/{self._model_path()}:generateContent"
        data = await self._send(
            "POST", url, headers=self._headers(),
            json_body=self._build_body(messages, tools, allow_tools),
        )

        candidates = data.get("candidates")
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            detail = f" (motif : {reason})" if reason else ""
            raise ProviderError(
                "invalid_response", f"Gemini n'a renvoyé aucune réponse{detail}."
            )

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []

        text_parts: list[str] = []
        calls: list[ToolCall] = []

        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if "text" in part and not part.get("thought"):
                text_parts.append(str(part["text"]))
            elif "functionCall" in part:
                call = part["functionCall"] or {}
                args = call.get("args")
                calls.append(
                    ToolCall(
                        id=f"call_{index}",
                        name=str(call.get("name", "")),
                        arguments=args if isinstance(args, dict) else {},
                    )
                )

        usage = data.get("usageMetadata") or {}
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=calls,
            usage={
                "input_tokens": int(usage.get("promptTokenCount", 0) or 0),
                "output_tokens": int(usage.get("candidatesTokenCount", 0) or 0),
            },
            finish_reason=candidate.get("finishReason"),
            provider_data=parts if calls else None,
        )

    async def list_models(self) -> list[str]:
        data = await self._send(
            "GET", f"{self.settings.base_url}/models?pageSize=200", headers=self._headers()
        )
        items = data.get("models")
        if not isinstance(items, list):
            raise ProviderError("invalid_response", "Liste de modèles inattendue.")
        return sorted(
            str(i["name"]).removeprefix("models/")
            for i in items
            if isinstance(i, dict) and "name" in i
            and "generateContent" in (i.get("supportedGenerationMethods") or ["generateContent"])
        )
