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

    async def _call(self, function, *args) -> dict[str, Any]:
        try:
            raw = await asyncio.to_thread(function, *args)
            return json.loads(raw)
        except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
            raise _translate(exc) from exc

    async def search(self, query, limit, language, category):
        return await self._call(self._engine.search, query, limit, language, category)

    async def get_document(self, document_id, start_line=None, end_line=None, max_bytes=None):
        return await self._call(
            self._engine.get_document, document_id, start_line, end_line, max_bytes
        )

    async def list_filters(self):
        return await self._call(self._engine.list_filters)

    async def index_health(self):
        """Échecs d'indexation, dossiers non inscriptibles, fichiers ignorés."""
        return await self._call(self._engine.indexing_issues)
