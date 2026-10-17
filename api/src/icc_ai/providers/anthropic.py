# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Connecteur Claude (API Messages d'Anthropic).

Particularités gérées :
- le message système est un champ séparé, pas un message ;
- les appels d'outils sont des blocs `tool_use`, et leurs résultats des blocs
  `tool_result` placés dans un message `user` (regroupés si consécutifs) ;
- `max_tokens` est obligatoire ;
- l'authentification passe par l'en-tête `x-api-key`.
"""

from __future__ import annotations

from .base import AssistantTurn, ChatProvider, Message, ToolCall, ToolSpec
from ..errors import ProviderError

API_VERSION = "2023-06-01"


class AnthropicProvider(ChatProvider):
    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.settings.api_key or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url}{path}"

    @staticmethod
    def _encode(messages: list[Message]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        encoded: list[dict] = []

        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue

            if message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
                # Les résultats consécutifs forment un seul message user.
                last = encoded[-1] if encoded else None
                if (
                    last
                    and last["role"] == "user"
                    and isinstance(last["content"], list)
                    and last["content"]
                    and last["content"][0].get("type") == "tool_result"
                ):
                    last["content"].append(block)
                else:
                    encoded.append({"role": "user", "content": [block]})
                continue

            if message.role == "assistant" and message.tool_calls:
                blocks: list[dict] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                encoded.append({"role": "assistant", "content": blocks})
                continue

            encoded.append({"role": message.role, "content": message.content})

        return "\n\n".join(system_parts), encoded

    def _build_body(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> dict:
        system, encoded = self._encode(messages)
        advanced = self.settings.advanced

        body: dict = {
            "model": self.settings.model,
            "max_tokens": advanced.max_output_tokens,
            "temperature": min(advanced.temperature, 1.0),
            "messages": encoded,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            if not allow_tools:
                body["tool_choice"] = {"type": "none"}
        return body

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec], allow_tools: bool = True
    ) -> AssistantTurn:
        data = await self._send(
            "POST", self._url("/v1/messages"),
            headers=self._headers(), json_body=self._build_body(messages, tools, allow_tools),
        )

        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError("invalid_response", "Réponse Claude sans contenu exploitable.")

        text_parts: list[str] = []
        calls: list[ToolCall] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=arguments if isinstance(arguments, dict) else {},
                        arguments_error=None if isinstance(arguments, dict)
                        else "Arguments d'outil non valides.",
                    )
                )

        usage = data.get("usage") or {}
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=calls,
            usage={
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
            },
            finish_reason=data.get("stop_reason"),
        )

    async def list_models(self) -> list[str]:
        data = await self._send("GET", self._url("/v1/models"), headers=self._headers())
        items = data.get("data")
        if not isinstance(items, list):
            raise ProviderError("invalid_response", "Liste de modèles inattendue.")
        return sorted(str(i["id"]) for i in items if isinstance(i, dict) and "id" in i)
