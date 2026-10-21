# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Accès direct au code indexé, sans IA : pour la CLI `icc`, le serveur MCP et les scripts.

Authentification : cookie de session ou jeton d'API (`Authorization: Bearer icc_pat_…`).
Mêmes plafonds par compte que la recherche IA (`CODECENTER_USER_RATE`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .api import RateLimitedUser, Services, _ENGINE_STATUS
from .errors import EngineError

router = APIRouter(prefix="/api/v1/code", tags=["Code"])


def _error(exc: EngineError) -> JSONResponse:
    return JSONResponse({"detail": exc.message, "code": f"engine_{exc.kind}"},
                        status_code=_ENGINE_STATUS.get(exc.kind, 503))


@router.get("/search")
async def code_search(
    services: Services, user: RateLimitedUser,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(10, ge=1, le=50),
    language: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=100),
):
    """Recherche brute dans l'index (notation par mots-clés), sans fournisseur IA."""
    try:
        return await services.engine.search(q.strip(), limit, language, category)
    except EngineError as exc:
        return _error(exc)


@router.get("/filters")
async def code_filters(services: Services, user: RateLimitedUser):
    try:
        return await services.engine.list_filters()
    except EngineError as exc:
        return _error(exc)


@router.get("/documents/{document_id}")
async def code_document(
    document_id: str, services: Services, user: RateLimitedUser,
    start_line: int | None = Query(None, ge=1), end_line: int | None = Query(None, ge=1),
):
    """Contenu d'un document (ou d'une plage de lignes)."""
    if len(document_id) > 64:
        raise HTTPException(422, "Identifiant invalide.")
    try:
        return await services.engine.get_document(document_id, start_line, end_line, None)
    except EngineError as exc:
        return _error(exc)
