# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Stockage persistant de la configuration globale des fournisseurs.

Un seul fournisseur est actif pour toute l'instance ; la configuration de
chaque fournisseur est conservée quand on en change. Les clés API sont
chiffrées au repos et ne sortent jamais de ce module en clair, sauf via
`runtime_settings` (usage interne du connecteur).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..catalog import PROVIDERS, default_advanced, get_spec
from ..errors import ConfigError
from .crypto import SecretBox, write_private_file
from .models import AdvancedSettings, ProviderConfigIn

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProviderRuntime:
    """Paramètres complets d'un fournisseur, secret inclus (usage interne)."""

    provider_id: str
    base_url: str
    model: str
    api_key: str | None
    advanced: AdvancedSettings

    def __repr__(self) -> str:  # ne jamais afficher la clé dans les logs
        return (
            f"ProviderRuntime({self.provider_id!r}, {self.base_url!r}, "
            f"{self.model!r}, api_key={'***' if self.api_key else None})"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConfigStore:
    def __init__(self, data_dir: Path, secret_box: SecretBox):
        self._dir = Path(data_dir) / "ai"
        self._file = self._dir / "providers.json"
        self._box = secret_box
        self._lock = threading.RLock()

    # -- lecture / écriture du fichier ---------------------------------

    def _load(self) -> dict:
        if not self._file.is_file():
            return {"version": SCHEMA_VERSION, "active": None, "providers": {}}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("Le fichier de configuration IA est illisible.") from exc
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            raise ConfigError("Version du fichier de configuration IA non prise en charge.")
        data.setdefault("providers", {})
        return data

    def _save(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode()
        write_private_file(self._file, payload)
        os.chmod(self._dir, 0o700)

    # -- vue publique (sans secret) -----------------------------------

    def public_view(self) -> dict:
        with self._lock:
            data = self._load()

        providers = {}
        for provider_id, spec in PROVIDERS.items():
            entry = data["providers"].get(provider_id)
            advanced = default_advanced(provider_id)
            if entry:
                advanced.update(entry.get("advanced", {}))
            providers[provider_id] = {
                "configured": entry is not None,
                "base_url": entry["base_url"] if entry else spec.default_base_url,
                "model": entry["model"] if entry else "",
                "api_key_set": bool(entry and entry.get("api_key_enc")),
                "advanced": advanced,
            }

        return {"active": data.get("active"), "providers": providers}

    # -- écriture -------------------------------------------------------

    def save(self, provider_id: str, config: ProviderConfigIn) -> None:
        """Enregistre un fournisseur et l'active si demandé."""
        get_spec(provider_id)
        advanced = AdvancedSettings.for_provider(provider_id, config.advanced)

        with self._lock:
            data = self._load()
            previous = data["providers"].get(provider_id, {})

            api_key_enc = previous.get("api_key_enc")
            if config.clear_api_key:
                api_key_enc = None
            elif config.api_key:
                api_key_enc = self._box.encrypt(config.api_key)

            data["providers"][provider_id] = {
                "base_url": config.base_url,
                "model": config.model,
                "api_key_enc": api_key_enc,
                "advanced": advanced.model_dump(),
                "updated_at": _now(),
            }

            if config.activate:
                data["active"] = provider_id

            self._save(data)

    # -- lecture interne ------------------------------------------------

    def resolve(self, provider_id: str, draft: ProviderConfigIn | None = None) -> ProviderRuntime:
        """Paramètres d'exécution : brouillon fusionné avec l'enregistré.

        Sert au test de connexion : la clé du brouillon prime, sinon la clé
        enregistrée est réutilisée sans jamais transiter par le frontend.
        """
        get_spec(provider_id)

        with self._lock:
            entry = self._load()["providers"].get(provider_id)

        stored_key = None
        if entry and entry.get("api_key_enc"):
            stored_key = self._box.decrypt(entry["api_key_enc"])

        if draft is not None:
            key = None if draft.clear_api_key else (draft.api_key or stored_key)
            return ProviderRuntime(
                provider_id, draft.base_url, draft.model, key,
                AdvancedSettings.for_provider(provider_id, draft.advanced),
            )

        if entry is None:
            raise ConfigError(f"Le fournisseur {provider_id} n'est pas configuré.")

        return ProviderRuntime(
            provider_id, entry["base_url"], entry["model"], stored_key,
            AdvancedSettings.for_provider(provider_id, entry.get("advanced")),
        )

    def active_runtime(self) -> ProviderRuntime | None:
        """Paramètres du fournisseur actif, ou None s'il n'y en a pas."""
        with self._lock:
            active = self._load().get("active")

        if not active or active not in PROVIDERS:
            return None
        return self.resolve(active)
