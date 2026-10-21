# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Passerelle vers le moteur Rust (`codecenter_engine`).

Python appelle directement les fonctions Rust exposées par PyO3 : aucun
appel HTTP interne. Les fonctions Rust sont bloquantes ; elles sont donc
exécutées dans un thread pour ne pas bloquer la boucle asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import OrderedDict
from typing import Any, Protocol

from .errors import EngineError


class EngineGateway(Protocol):
    """Contrat utilisé par les outils et l'orchestrateur (remplaçable en test)."""

    async def search(
        self, query: str, limit: int, language: str | None, category: str | None
    ) -> dict[str, Any]: ...

    async def get_document(
        self,
        document_id: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]: ...

    async def list_filters(self) -> dict[str, Any]: ...

    async def index_health(self) -> dict[str, Any]: ...


def _cache_size() -> int:
    """Entrées du cache de recherche (`CODECENTER_SEARCH_CACHE`, 0 = désactivé)."""
    try:
        return max(0, min(5000, int(os.environ.get("CODECENTER_SEARCH_CACHE", "128"))))
    except ValueError:
        return 128


def _translate(exc: Exception) -> EngineError:
    """Convertit les exceptions Python levées par Rust en EngineError."""
    message = str(exc)
    if isinstance(exc, ValueError):
        return EngineError("invalid", message)
    if isinstance(exc, LookupError):
        return EngineError("not_found", message)
    if isinstance(exc, PermissionError):
        return EngineError("forbidden", message)
    return EngineError("internal", message)


class RustEngine:
    """Implémentation réelle, au-dessus du module PyO3."""

    def __init__(self) -> None:
        import codecenter_engine  # import tardif : absent des tests unitaires

        self._engine = codecenter_engine
        self._cache: OrderedDict[tuple, str] = OrderedDict()
        self._cache_lock = threading.Lock()

    async def _call(self, function, *args) -> dict[str, Any]:
        try:
            raw = await asyncio.to_thread(function, *args)
            return json.loads(raw)
        except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
            raise _translate(exc) from exc

    def _generation(self) -> int | None:
        """Compteur qui change à chaque modification de l'index (None : moteur plus ancien)."""
        try:
            return int(json.loads(self._engine.scan_status()).get("generation", 0))
        except Exception:
            return None

    async def search(self, query, limit, language, category):
        """Recherche, avec un petit cache invalidé dès que l'index change."""
        size = _cache_size()
        generation = self._generation() if size else None
        if generation is None:
            return await self._call(self._engine.search, query, limit, language, category)
        key = (generation, query.strip().lower(), limit, language, category)
        with self._cache_lock:
            raw = self._cache.get(key)
            if raw is not None:
                self._cache.move_to_end(key)
        if raw is None:
            try:
                raw = await asyncio.to_thread(self._engine.search, query, limit, language, category)
            except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
                raise _translate(exc) from exc
            with self._cache_lock:
                self._cache[key] = raw
                while len(self._cache) > size:
                    self._cache.popitem(last=False)
        return json.loads(raw)

    async def scan_status(self) -> dict[str, Any]:
        """État du passage d'indexation en cours (compteurs, durée, génération de l'index)."""
        return await self._call(self._engine.scan_status)

    def rescan(self) -> None:
        """Demande un passage d'indexation immédiat."""
        self._engine.rescan_now()

    async def get_document(self, document_id, start_line=None, end_line=None, max_bytes=None):
        return await self._call(
            self._engine.get_document, document_id, start_line, end_line, max_bytes
        )

    async def list_filters(self):
        return await self._call(self._engine.list_filters)

    async def index_health(self):
        """Échecs d'indexation, dossiers non inscriptibles, fichiers ignorés."""
        return await self._call(self._engine.indexing_issues)
