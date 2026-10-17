# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Fabrique de connecteurs : choisit la classe selon le protocole."""

from __future__ import annotations

import httpx

from ..catalog import get_spec
from ..config.store import ProviderRuntime
from .anthropic import AnthropicProvider
from .base import ChatProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

_BY_PROTOCOL = {
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def create_provider(
    settings: ProviderRuntime,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatProvider:
    """Instancie le connecteur du fournisseur indiqué dans `settings`."""
    spec = get_spec(settings.provider_id)
    return _BY_PROTOCOL[spec.protocol](settings, transport)
