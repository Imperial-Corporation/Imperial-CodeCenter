# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Assemblage des services IA (configuration, moteur, orchestrateur)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config.crypto import SecretBox
from .accounts import AccountStore
from .chat import ChatService
from .config.store import ConfigStore
from .crawl_jobs import CrawlManager
from .library import Library
from .engine_gateway import EngineGateway, RustEngine
from .service import SearchService

# Racine du dépôt (…/api/src/icc_ai/services.py -> racine).
ROOT = Path(__file__).resolve().parents[3]


@dataclass
class AIServices:
    store: ConfigStore | None
    engine: EngineGateway
    search: SearchService
    chat: ChatService | None = None
    library: Library | None = None
    crawler: CrawlManager | None = None
    accounts: AccountStore | None = None

    def __post_init__(self) -> None:
        if self.chat is None:  # le chat s'appuie toujours sur le même pipeline de recherche
            self.chat = ChatService(self.search)


def data_dir() -> Path:
    """Dossier de données : `CODECENTER_DATA_DIR`, sinon `.local/data` du dépôt."""
    return Path(os.environ.get("CODECENTER_DATA_DIR") or ROOT / ".local" / "data")


def build_services(engine: EngineGateway | None = None) -> AIServices:
    """Construit les services avec la configuration de l'environnement."""
    box = SecretBox.from_environment(ROOT / ".local")
    # La configuration IA (fournisseurs, clés) est propre à chaque compte (voir config/user_store.py) :
    # il n'y a plus de configuration globale. `box` ne protège plus que le jeton GitHub du crawler.
    store = None
    engine = engine or RustEngine()
    search = SearchService(store, engine)
    library = Library.from_environment(data_dir())
    crawler = CrawlManager(data_dir(), library.root, box)
    accounts = AccountStore.from_environment(data_dir())
    return AIServices(store, engine, search, ChatService(search), library, crawler, accounts)
