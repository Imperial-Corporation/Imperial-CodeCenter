"""Connecteur pour les API compatibles OpenAI (OpenAI, Groq, OmniRoute).

Particularités gérées :
- les arguments d'outils arrivent sous forme de chaîne JSON ;
- OpenAI exige `max_completion_tokens` (et refuse `temperature` sur ses
  modèles de raisonnement), Groq l'accepte aussi, les passerelles
  génériques attendent plutôt `max_tokens` ;
- la clé API est facultative pour une passerelle locale.
"""

from __future__ import annotations

import json

import httpx

from .base import (
    AssistantTurn, ChatProvider, Message, ToolCall, ToolSpec, parse_json_arguments,
)
from ..errors import ProviderError, ToolsUnsupportedError


def merge_sse_chunks(text: str) -> dict | None:
    """Reconstitue une réponse `chat.completion` depuis un flux SSE.

    Certaines passerelles (OmniRoute, combos de modèles…) répondent en
    streaming même quand on ne le demande pas. Les fragments `delta` sont
    assemblés : texte, appels d'outils (arguments concaténés par index),
    motif d'arrêt et consommation. Retourne None si le texte n'est pas du SSE.
    """
    content: list[str] = []
    calls: dict[int, dict] = {}
    finish = None
    usage: dict = {}
    seen = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        seen = True

        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]

        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            finish = choice.get("finish_reason") or finish
            part = choice.get("delta") or choice.get("message") or {}
            if part.get("content"):
                content.append(str(part["content"]))
            for position, call in enumerate(part.get("tool_calls") or []):
                index = call.get("index", position)
                slot = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["name"] += function.get("name") or ""
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments)
                slot["arguments"] += arguments or ""

    if not seen:
        return None

    message: dict = {"content": "".join(content) or None}
    if calls:
        message["tool_calls"] = [
            {"id": slot["id"], "type": "function",
             "function": {"name": slot["name"], "arguments": slot["arguments"]}}
            for _, slot in sorted(calls.items())
        ]
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}

# Modèles OpenAI de raisonnement : température fixée par le fournisseur.
_NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class OpenAICompatProvider(ChatProvider):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url}{path}"

    # -- traduction des messages ---------------------------------------

    @staticmethod
    def _encode_message(message: Message) -> dict:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        if message.role == "assistant" and message.tool_calls:
            import json

            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        return {"role": message.role, "content": message.content}

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> dict:
        advanced = self.settings.advanced
        provider = self.settings.provider_id
        model = self.settings.model

        body: dict = {
            "model": model,
            "messages": [self._encode_message(m) for m in messages],
            # Réponse unique : évite le streaming par défaut de certaines passerelles.
            "stream": False,
        }

        if provider in ("openai", "groq"):
            body["max_completion_tokens"] = advanced.max_output_tokens
        else:
            body["max_tokens"] = advanced.max_output_tokens

        if not (provider == "openai" and model.lower().startswith(_NO_TEMPERATURE_PREFIXES)):
            body["temperature"] = advanced.temperature

        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto" if allow_tools else "none"

        return body

    def _parse_body(self, response: httpx.Response) -> dict:
        """JSON classique, sinon flux SSE recomposé, sinon erreur explicite."""
        try:
            return response.json()
        except ValueError:
            merged = merge_sse_chunks(response.text)
            if merged is not None:
                return merged
            raise ProviderError("invalid_response", self._describe_bad_body(response)) from None

    # -- API publique -----------------------------------------------------

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> AssistantTurn:
        try:
            data = await self._send(
                "POST", self._url("/chat/completions"),
                headers=self._headers(), json_body=self._build_body(messages, tools, allow_tools),
            )
        except ProviderError as exc:
            lowered = exc.message.lower()
            if tools and exc.kind == "bad_request" and ("tool" in lowered or "function" in lowered):
                raise ToolsUnsupportedError() from exc
            raise

        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "invalid_response", "Réponse du fournisseur sans message exploitable."
            ) from exc

        calls: list[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments, error = parse_json_arguments(function.get("arguments"))
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name", "")),
                    arguments=arguments,
                    arguments_error=error,
                )
            )

        usage = data.get("usage") or {}
        return AssistantTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            usage={
                "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            },
            finish_reason=choice.get("finish_reason"),
        )

    async def list_models(self) -> list[str]:
        data = await self._send("GET", self._url("/models"), headers=self._headers())
        items = data.get("data")
        if not isinstance(items, list):
            raise ProviderError("invalid_response", "Liste de modèles inattendue.")
        return sorted(str(i["id"]) for i in items if isinstance(i, dict) and "id" in i)
