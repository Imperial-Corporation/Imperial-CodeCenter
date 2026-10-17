# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Connecteur Ollama (API native `/api/chat`).

Particularités gérées :
- les arguments d'outils sont des objets JSON, pas des chaînes ;
- les appels n'ont pas d'identifiant : des identifiants locaux sont créés ;
- un modèle sans support des outils répond 400 : l'erreur est typée
  (`ToolsUnsupportedError`) pour que l'orchestrateur réessaie sans outils ;
- la fenêtre de contexte se règle par requête (`num_ctx`).
"""

from __future__ import annotations

from .base import AssistantTurn, ChatProvider, Message, ToolCall, ToolSpec
from ..errors import ProviderError, ToolsUnsupportedError


class OllamaProvider(ChatProvider):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:  # utile derrière un proxy authentifié
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    @staticmethod
    def _encode(message: Message) -> dict:
        if message.role == "tool":
            return {"role": "tool", "tool_name": message.name or "", "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in message.tool_calls
                ],
            }
        return {"role": message.role, "content": message.content}

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> dict:
        advanced = self.settings.advanced
        body: dict = {
            "model": self.settings.model,
            "stream": False,
            "messages": [self._encode(m) for m in messages],
            "options": {
                "temperature": advanced.temperature,
                "num_predict": advanced.max_output_tokens,
                "num_ctx": advanced.context_window,
            },
        }
        # Ollama n'a pas de tool_choice : sans outils, on n'envoie pas la liste.
        if tools and allow_tools:
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
        return body

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> AssistantTurn:
        try:
            data = await self._send(
                "POST", f"{self.settings.base_url}/api/chat",
                headers=self._headers(), json_body=self._build_body(messages, tools, allow_tools),
            )
        except ProviderError as exc:
            if tools and exc.kind == "bad_request" and "tool" in exc.message.lower():
                raise ToolsUnsupportedError() from exc
            raise

        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderError("invalid_response", "Réponse Ollama sans message exploitable.")

        calls: list[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            calls.append(
                ToolCall(
                    id=f"call_{index}",
                    name=str(function.get("name", "")),
                    arguments=arguments if isinstance(arguments, dict) else {},
                    arguments_error=None if isinstance(arguments, dict)
                    else "Arguments d'outil non valides.",
                )
            )

        return AssistantTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            usage={
                "input_tokens": int(data.get("prompt_eval_count", 0) or 0),
                "output_tokens": int(data.get("eval_count", 0) or 0),
            },
            finish_reason=data.get("done_reason"),
        )

    async def list_models(self) -> list[str]:
        data = await self._send(
            "GET", f"{self.settings.base_url}/api/tags", headers=self._headers()
        )
        items = data.get("models")
        if not isinstance(items, list):
            raise ProviderError("invalid_response", "Liste de modèles inattendue.")
        return sorted(str(i["name"]) for i in items if isinstance(i, dict) and "name" in i)
