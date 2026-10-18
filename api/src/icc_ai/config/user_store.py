# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Configuration IA d'un compte : fournisseurs, modèles et clés API.

Même interface que `ConfigStore` (vue publique, enregistrement, résolution),
mais le fichier global est remplacé par un bloc chiffré propre au compte :
AES-256-GCM avec la clé de données du compte, elle-même protégée par le mot
de passe (voir `accounts.py`). Un compte ne peut ni lire ni utiliser la
configuration d'un autre.
"""

from __future__ import annotations

import threading

from ..accounts import AccountStore
from ..errors import ConfigError
from ..netguard import check_provider_url
from .store import SCHEMA_VERSION, ConfigStore


class _InnerBox:
    """Le bloc entier est déjà chiffré : les clés y sont stockées telles quelles."""

    def encrypt(self, plaintext: str) -> str:
        return "inner:" + plaintext

    def decrypt(self, token: str) -> str:
        if not token.startswith("inner:"):
            raise ConfigError("Format de secret inconnu.")
        return token[len("inner:"):]


class UserConfigStore(ConfigStore):
    def __init__(self, accounts: AccountStore, user_id: str, dek: bytes):  # noqa: super().__init__ volontairement absent
        self._accounts = accounts
        self._user_id = user_id
        self._dek = dek
        self._box = _InnerBox()
        self._lock = threading.RLock()

    def _load(self) -> dict:
        data = self._accounts.load_ai_config(self._user_id, self._dek)
        if data is None:
            return {"version": SCHEMA_VERSION, "active": None, "providers": {}}
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            raise ConfigError("Version de la configuration IA non prise en charge.")
        data.setdefault("providers", {})
        return data

    def _save(self, data: dict) -> None:
        self._accounts.save_ai_config(self._user_id, self._dek, data)

    # Chaque compte choisit l'URL de son fournisseur : refus des adresses internes dangereuses.
    def save(self, provider_id, config):
        check_provider_url(config.base_url)
        return super().save(provider_id, config)

    def resolve(self, provider_id, draft=None):
        runtime = super().resolve(provider_id, draft)
        check_provider_url(runtime.base_url)
        return runtime
