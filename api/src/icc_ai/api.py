# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Routes FastAPI de l'IA : configuration des fournisseurs et recherche.

FastAPI reste la seule interface entre le frontend et le backend. Les
routes de configuration modifient un état global de l'instance : si
`CODECENTER_ADMIN_TOKEN` est défini, elles exigent l'en-tête
`Authorization: Bearer <jeton>` ; dans tous les cas, une requête
inter-sites (en-tête Origin différent de l'hôte) est refusée.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .catalog import PROVIDERS, catalog_payload
from .config.models import ProviderConfigIn
from .errors import ChatError, ConfigError, EngineError, ProviderError, SearchCancelled
from .providers.registry import create_provider
from .response import SearchResponse
from .service import SearchParams
from .services import AIServices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Recherches simultanées au plus : protège le moteur et le fournisseur.
_search_gate = asyncio.Semaphore(4)

_PROVIDER_STATUS = {
    "timeout": 504, "rate_limit": 429, "auth": 502, "network": 502,
    "not_found": 502, "bad_request": 502, "server": 502,
    "invalid_response": 502, "tools_unsupported": 502,
}
_ENGINE_STATUS = {"invalid": 422, "not_found": 404, "forbidden": 403, "internal": 503}


def get_services(request: Request) -> AIServices:
    services = getattr(request.app.state, "ai", None)
    if services is None:
        raise HTTPException(503, "Les services IA ne sont pas initialisés.")
    return services


Services = Annotated[AIServices, Depends(get_services)]


def check_origin(request: Request) -> None:
    """Refuse une requête dont l'en-tête Origin n'est pas l'hôte (CSRF)."""
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        if urlsplit(origin).netloc != host:
            raise HTTPException(403, "Requête inter-sites refusée.")


def require_admin(request: Request) -> None:
    """Protège les routes qui modifient la configuration globale."""
    check_origin(request)

    token = os.environ.get("CODECENTER_ADMIN_TOKEN", "").strip()
    if token:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Jeton d'administration requis ou invalide.")


Admin = Depends(require_admin)


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def _check_provider(provider_id: str) -> None:
    if provider_id not in PROVIDERS:
        raise HTTPException(404, "Fournisseur inconnu.")


# --------------------------------------------------------------------------
# Configuration des fournisseurs
# --------------------------------------------------------------------------

@router.get("/ai/catalog", tags=["AI"])
def ai_catalog():
    """Fournisseurs pris en charge, valeurs par défaut et réglages avancés."""
    return catalog_payload()


@router.get("/ai/config", tags=["AI"], dependencies=[Admin])
def ai_config(services: Services):
    """Configuration enregistrée, sans aucun secret (`api_key_set` seulement)."""
    try:
        return services.store.public_view()
    except ConfigError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/ai/providers/{provider_id}/test", tags=["AI"], dependencies=[Admin])
async def ai_test(provider_id: str, config: ProviderConfigIn, services: Services):
    """Teste la connexion avec le brouillon du formulaire (clé enregistrée si absente)."""
    _check_provider(provider_id)
    try:
        runtime = services.store.resolve(provider_id, config)
        report = await create_provider(runtime).test_connection()
    except ProviderError as exc:
        return {"ok": False, "message": exc.message, "kind": exc.kind, "latency_ms": 0, "models": []}
    except ConfigError as exc:
        return {"ok": False, "message": str(exc), "kind": "config", "latency_ms": 0, "models": []}

    return {
        "ok": report.ok, "message": report.message, "kind": None,
        "latency_ms": report.latency_ms, "models": report.models[:200],
    }


@router.put("/ai/providers/{provider_id}", tags=["AI"], dependencies=[Admin])
def ai_save(provider_id: str, config: ProviderConfigIn, services: Services):
    """Enregistre la configuration d'un fournisseur et l'active si demandé."""
    _check_provider(provider_id)
    try:
        services.store.save(provider_id, config)
        return services.store.public_view()
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422 if isinstance(exc, ValueError) else 500, str(exc)) from exc


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------

Depth = Literal["auto", "overview", "focused", "deep"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    language: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    # auto : aperçu simple pour une question générale, approfondi sur demande.
    depth: Depth = "auto"


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2000)
    language: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    depth: Depth = "auto"


_CHAT_STATUS = {"invalid": 422, "not_found": 404, "too_long": 409, "busy": 409}


async def _run_cancellable(request: Request, factory):
    """Exécute `factory()` sous le garde-fou de concurrence ; annule si le client part."""
    async with _search_gate:
        task = asyncio.ensure_future(factory())
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise SearchCancelled()
                await asyncio.wait({task}, timeout=0.25)
            return task.result()
        except SearchCancelled:
            logger.info("Requête IA annulée par le client")
            return _error(499, "Recherche annulée.", "cancelled")
        except asyncio.CancelledError:
            task.cancel()
            raise
        except ChatError as exc:
            return _error(_CHAT_STATUS.get(exc.kind, 422), exc.message, f"chat_{exc.kind}")
        except ProviderError as exc:
            return _error(_PROVIDER_STATUS.get(exc.kind, 502), exc.message, f"provider_{exc.kind}")
        except EngineError as exc:
            return _error(_ENGINE_STATUS.get(exc.kind, 503), exc.message, f"engine_{exc.kind}")
        except ConfigError as exc:
            return _error(503, str(exc), "config")
        except ValidationError:
            logger.exception("Réponse incohérente")
            return _error(502, "Réponse du service IA incohérente.", "invalid_response")
        except Exception:
            logger.exception("Échec inattendu de la requête IA")
            return _error(500, "Erreur interne pendant la requête.", "internal")


def _chat(services: AIServices):
    if services.chat is None:
        raise HTTPException(503, "Le chat n'est pas initialisé.")
    return services.chat


@router.post("/search", tags=["Search"], response_model=SearchResponse)
async def search(body: SearchRequest, request: Request, services: Services):
    """Recherche assistée par IA ; ouvre une conversation si l'IA répond."""
    params = SearchParams(body.query.strip(), body.limit, body.language, body.category)
    if not params.query:
        raise HTTPException(422, "La requête est vide.")
    chat = _chat(services)
    return await _run_cancellable(request, lambda: chat.search(params, body.depth))


@router.post("/chat", tags=["Search"], response_model=SearchResponse)
async def chat_turn(body: ChatRequest, request: Request, services: Services):
    """Message suivant d'une conversation (les outils restent disponibles)."""
    chat = _chat(services)
    result = await _run_cancellable(
        request,
        lambda: chat.reply(body.conversation_id, body.message, depth=body.depth,
                           language=body.language, category=body.category),
    )
    if isinstance(result, SearchResponse) and result.status == "ok":
        _persist_turn(request, services, body.conversation_id, result)
    return result


def _persist_turn(request: Request, services: AIServices, conversation_id: str, response: SearchResponse) -> None:
    """Ajoute le tour à la discussion enregistrée, si elle l'est et que son propriétaire est connecté."""
    target = services.chat.saved_target(conversation_id) if services.chat else None
    if target is None or services.accounts is None:
        return
    from .accounts_api import user_from_request  # import tardif : accounts_api dépend de ce module

    user = user_from_request(request, services)
    if user is None or user["id"] != target[1]:
        return
    try:
        services.accounts.append_turn(user["id"], target[0], services.chat.snapshot(conversation_id)[-1])
    except Exception as exc:  # la réponse reste valable même si l'enregistrement échoue
        logger.warning("Enregistrement du tour impossible : %s", exc)
        response.warnings.append("Ce message n'a pas pu être ajouté à la discussion enregistrée.")
