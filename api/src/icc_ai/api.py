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
from .config.user_store import UserConfigStore
from .netguard import ForbiddenProviderUrl
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

COOKIE_NAME = "icc_session"


def user_from_request(request: Request, services: AIServices) -> dict | None:
    if services.accounts is None:
        return None
    token = request.cookies.get(COOKIE_NAME)
    user = services.accounts.user_for_token(token)
    if user is None:
        return None
    try:
        unlocked = services.accounts.dek_for_token(token, user["id"]) is not None
    except Exception:
        unlocked = False
    # Une session sans clé de données (ouverte avant le chiffrement par compte) n'est plus valable.
    return user if unlocked else None


def require_user(request: Request, services: Services) -> dict:
    """Compte connecté obligatoire : la recherche, le chat et la configuration IA en dépendent."""
    user = user_from_request(request, services)
    if user is None:
        raise HTTPException(401, "Connexion requise : crée un compte ou connecte-toi pour utiliser l'IA.")
    return user


User = Annotated[dict, Depends(require_user)]


class _UserRate:
    """Plafond de requêtes IA par compte et par minute (`CODECENTER_USER_RATE`, 0 = illimité)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, user_id: str) -> int:
        import time

        try:
            limit = int(os.environ.get("CODECENTER_USER_RATE", "30"))
        except ValueError:
            limit = 30
        if limit <= 0:
            return 0
        now = time.monotonic()
        hits = [t for t in self._hits.get(user_id, []) if now - t < 60]
        if len(hits) >= limit:
            self._hits[user_id] = hits
            return max(1, int(60 - (now - hits[0])))
        hits.append(now)
        self._hits[user_id] = hits
        if len(self._hits) > 5000:  # borne la mémoire
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 60}
        return 0


_user_rate = _UserRate()


def rate_limited_user(user: User) -> dict:
    wait = _user_rate.check(user["id"])
    if wait:
        raise HTTPException(429, "Trop de requêtes : patiente un instant avant de relancer.",
                            headers={"Retry-After": str(wait)})
    return user


RateLimitedUser = Annotated[dict, Depends(rate_limited_user)]


def user_config(request: Request, services: Services, user: User) -> UserConfigStore:
    """Configuration IA du compte, déverrouillée par la session en cours (jamais celle d'un autre)."""
    from .accounts import AccountError

    try:
        dek = services.accounts.dek_for_token(request.cookies.get(COOKIE_NAME), user["id"])
    except AccountError:
        dek = None
    if dek is None:  # session sans clé (ancienne session) : reconnexion nécessaire pour déverrouiller
        raise HTTPException(401, "Session à renouveler : reconnecte-toi pour déverrouiller tes clés.")
    return UserConfigStore(services.accounts, user["id"], dek)


UserConfig = Annotated[UserConfigStore, Depends(user_config)]


def check_origin(request: Request) -> None:
    """Refuse une requête dont l'en-tête Origin n'est pas l'hôte (CSRF)."""
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        if urlsplit(origin).netloc != host:
            raise HTTPException(403, "Requête inter-sites refusée.")


def require_admin(request: Request, services: Services) -> None:
    """Protège les routes d'administration (import, crawler, santé de l'index).

    Autorisé : le porteur du jeton `CODECENTER_ADMIN_TOKEN` s'il est défini,
    ou le compte administrateur (le premier compte créé sur l'instance).
    Un visiteur ou un autre compte est refusé.
    """
    check_origin(request)

    token = os.environ.get("CODECENTER_ADMIN_TOKEN", "").strip()
    if token:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if supplied and hmac.compare_digest(supplied.encode(), token.encode()):
            return

    user = user_from_request(request, services)
    if user is not None and services.accounts is not None and services.accounts.is_admin(user["id"]):
        return
    if user is not None:
        raise HTTPException(403, "Réservé à l'administrateur de l'instance (le premier compte créé).")
    raise HTTPException(401, "Connexion administrateur requise : connecte-toi avec le premier compte créé "
                             "ou fournis le jeton d'administration.")


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


@router.get("/ai/config", tags=["AI"])
def ai_config(store: UserConfig):
    """Configuration du compte, sans aucun secret (`api_key_set` seulement)."""
    try:
        return store.public_view()
    except ConfigError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/ai/providers/{provider_id}/test", tags=["AI"], dependencies=[Depends(check_origin)])
async def ai_test(provider_id: str, config: ProviderConfigIn, store: UserConfig):
    """Teste la connexion avec le brouillon du formulaire (clé enregistrée si absente)."""
    _check_provider(provider_id)
    try:
        runtime = store.resolve(provider_id, config)
        report = await create_provider(runtime).test_connection()
    except ProviderError as exc:
        return {"ok": False, "message": exc.message, "kind": exc.kind, "latency_ms": 0, "models": []}
    except ConfigError as exc:
        return {"ok": False, "message": str(exc), "kind": "config", "latency_ms": 0, "models": []}

    return {
        "ok": report.ok, "message": report.message, "kind": None,
        "latency_ms": report.latency_ms, "models": report.models[:200],
    }


@router.put("/ai/providers/{provider_id}", tags=["AI"], dependencies=[Depends(check_origin)])
def ai_save(provider_id: str, config: ProviderConfigIn, store: UserConfig):
    """Enregistre la configuration d'un fournisseur et l'active si demandé."""
    _check_provider(provider_id)
    try:
        store.save(provider_id, config)
        return store.public_view()
    except (ForbiddenProviderUrl, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(500, str(exc)) from exc


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


@router.post("/search", tags=["Search"], response_model=SearchResponse, dependencies=[Depends(check_origin)])
async def search(body: SearchRequest, request: Request, services: Services, user: RateLimitedUser, store: UserConfig):
    """Recherche assistée par IA avec la configuration du compte ; ouvre une conversation si l'IA répond."""
    params = SearchParams(body.query.strip(), body.limit, body.language, body.category)
    if not params.query:
        raise HTTPException(422, "La requête est vide.")
    chat = _chat(services)
    return await _run_cancellable(
        request, lambda: chat.search(params, body.depth, config=store, user_id=user["id"]))


@router.post("/chat", tags=["Search"], response_model=SearchResponse, dependencies=[Depends(check_origin)])
async def chat_turn(body: ChatRequest, request: Request, services: Services, user: RateLimitedUser, store: UserConfig):
    """Message suivant d'une conversation (les outils restent disponibles)."""
    chat = _chat(services)
    result = await _run_cancellable(
        request,
        lambda: chat.reply(body.conversation_id, body.message, depth=body.depth,
                           language=body.language, category=body.category, config=store, user_id=user["id"]),
    )
    if isinstance(result, SearchResponse) and result.status == "ok":
        _persist_turn(services, user, body.conversation_id, result)
    return result


def _persist_turn(services: AIServices, user: dict, conversation_id: str, response: SearchResponse) -> None:
    """Ajoute le tour à la discussion enregistrée, si elle l'est et que son propriétaire est connecté."""
    target = services.chat.saved_target(conversation_id) if services.chat else None
    if target is None or services.accounts is None:
        return
    if user["id"] != target[1]:
        return
    try:
        services.accounts.append_turn(user["id"], target[0], services.chat.snapshot(conversation_id, user["id"])[-1])
    except Exception as exc:  # la réponse reste valable même si l'enregistrement échoue
        logger.warning("Enregistrement du tour impossible : %s", exc)
        response.warnings.append("Ce message n'a pas pu être ajouté à la discussion enregistrée.")
