# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Modèles de validation de la configuration des fournisseurs."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..catalog import ADVANCED_FIELDS, default_advanced

_MODEL_RE = re.compile(r"^[A-Za-z0-9][\w.:/@+\-]{0,199}$")


def validate_base_url(value: str) -> str:
    """Accepte uniquement http(s) sans identifiants, requête ni fragment."""
    value = value.strip().rstrip("/")
    parts = urlsplit(value)

    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("L'URL doit commencer par http:// ou https://.")
    if parts.username or parts.password:
        raise ValueError("L'URL ne doit pas contenir d'identifiants.")
    if parts.query or parts.fragment:
        raise ValueError("L'URL ne doit pas contenir de paramètres ni d'ancre.")
    if len(value) > 300:
        raise ValueError("URL trop longue.")

    return value


class AdvancedSettings(BaseModel):
    """Réglages avancés, bornés selon le catalogue."""

    model_config = ConfigDict(extra="forbid")

    timeout_s: int = 60
    max_output_tokens: int = 2048
    context_window: int = 32000
    temperature: float = 0.2
    max_tool_calls: int = 6

    @classmethod
    def for_provider(cls, provider_id: str, overrides: dict | None = None) -> "AdvancedSettings":
        values = default_advanced(provider_id)
        values.update(overrides or {})
        return cls(**values)

    def model_post_init(self, _context) -> None:  # noqa: D401
        for field in ADVANCED_FIELDS:
            value = getattr(self, field.key)
            if not (field.minimum <= value <= field.maximum):
                raise ValueError(
                    f"{field.label} doit être entre {field.minimum:g} et {field.maximum:g}."
                )


class ProviderConfigIn(BaseModel):
    """Corps d'une requête de test ou d'enregistrement."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str
    # None = conserver la clé enregistrée ; une chaîne remplace la clé.
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    advanced: dict = Field(default_factory=dict)
    activate: bool = False

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return validate_base_url(value)

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        value = value.strip()
        if not _MODEL_RE.match(value):
            raise ValueError("Nom de modèle invalide.")
        return value

    @field_validator("api_key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(c.isspace() for c in value):
            raise ValueError("La clé API ne doit pas contenir d'espaces.")
        return value or None
