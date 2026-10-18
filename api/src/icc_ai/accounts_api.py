# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Routes des comptes et des discussions enregistrées.

Cookie de session `icc_session` : HttpOnly, SameSite=Lax, Secure derrière
HTTPS. Les requêtes qui modifient l'état refusent un en-tête Origin étranger.
La recherche, le chat et la configuration IA exigent un compte (clés chiffrées par compte).
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .accounts import SESSION_TTL_S, AccountError, AccountStore
from .api import COOKIE_NAME, Services, User, check_origin, require_user, user_from_request
from .errors import ChatError
from .services import AIServices

router = APIRouter(prefix="/api/v1", dependencies=[Depends(check_origin)])


def _store(services: AIServices) -> AccountStore:
    if services.accounts is None:
        raise HTTPException(503, "Les comptes ne sont pas disponibles sur cette instance.")
    return services.accounts


def _secure(request: Request) -> bool:
    forced = os.environ.get("CODECENTER_FORCE_HTTPS", "").strip().lower() in {"1", "true", "on", "yes", "oui"}
    return forced or request.url.scheme == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_S, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _fail(exc: AccountError) -> JSONResponse:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status, headers=headers)


# --------------------------------------------------------------------------
# Comptes
# --------------------------------------------------------------------------

class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class PasswordOnly(BaseModel):
    password: str = Field(max_length=256)


@router.get("/auth/me", tags=["Accounts"])
def me(request: Request, services: Services):
    enabled = services.accounts is not None
    user = user_from_request(request, services)
    return {
        "enabled": enabled,
        "registration_open": bool(enabled and services.accounts.registration_open),
        "user": {"username": user["username"]} if user else None,
    }


@router.post("/auth/register", tags=["Accounts"])
async def register(body: Credentials, request: Request, response: Response, services: Services):
    store = _store(services)
    try:
        user = await asyncio.to_thread(store.register, body.username, body.password, _client_ip(request))
    except AccountError as exc:
        return _fail(exc)
    _set_cookie(response, request, store.create_session(user["id"], user["dek"]))
    return {"user": {"username": user["username"]}}


@router.post("/auth/login", tags=["Accounts"])
async def login(body: Credentials, request: Request, response: Response, services: Services):
    store = _store(services)
    try:
        user = await asyncio.to_thread(store.authenticate, body.username, body.password, _client_ip(request))
    except AccountError as exc:
        return _fail(exc)
    _set_cookie(response, request, store.create_session(user["id"], user["dek"]))
    return {"user": {"username": user["username"]}}


@router.post("/auth/logout", tags=["Accounts"])
def logout(request: Request, response: Response, services: Services):
    if services.accounts is not None:
        services.accounts.end_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/password", tags=["Accounts"])
async def change_password(body: PasswordChange, request: Request, user: User, services: Services):
    try:
        await asyncio.to_thread(services.accounts.change_password, user["id"], body.current, body.new,
                                request.cookies.get(COOKIE_NAME))
    except AccountError as exc:
        return _fail(exc)
    return {"ok": True}


@router.post("/auth/delete", tags=["Accounts"])
async def delete_account(body: PasswordOnly, response: Response, user: User, services: Services):
    try:
        await asyncio.to_thread(services.accounts.delete_account, user["id"], body.password)
    except AccountError as exc:
        return _fail(exc)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# --------------------------------------------------------------------------
# Discussions enregistrées
# --------------------------------------------------------------------------

class SaveChat(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)


class RenameChat(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.get("/chats", tags=["Chats"])
def list_chats(user: User, services: Services):
    return {"chats": services.accounts.list_chats(user["id"])}


@router.post("/chats", tags=["Chats"])
def save_chat(body: SaveChat, user: User, services: Services):
    """Rend définitive la discussion en cours ; la suite s'y ajoute automatiquement."""
    chat = services.chat
    try:
        turns = chat.snapshot(body.conversation_id, user["id"])
        if chat.saved_target(body.conversation_id):
            return _fail(AccountError("already_saved", "Cette discussion est déjà enregistrée.", 409))
        title = body.title or (turns[0]["question"] if turns else "Discussion")
        saved = services.accounts.save_chat(user["id"], title, turns)
    except ChatError as exc:
        return JSONResponse({"detail": exc.message, "code": f"chat_{exc.kind}"}, status_code=404)
    except AccountError as exc:
        return _fail(exc)
    chat.mark_saved(body.conversation_id, saved["id"], user["id"])
    return saved


@router.post("/chats/{chat_id}/open", tags=["Chats"])
def open_chat(chat_id: str, user: User, services: Services):
    """Rouvre une discussion enregistrée : nouvelle conversation en mémoire, tours à afficher."""
    try:
        saved = services.accounts.get_chat(user["id"], chat_id)
    except AccountError as exc:
        return _fail(exc)
    conversation_id = services.chat.restore(saved["turns"], chat_id=chat_id, owner_id=user["id"])
    for turn in saved["turns"]:  # la même conversation est reconnue pour la suite
        turn.setdefault("response", {}).setdefault("meta", {})["conversation_id"] = conversation_id
    return {"id": chat_id, "title": saved["title"], "conversation_id": conversation_id, "turns": saved["turns"]}


@router.patch("/chats/{chat_id}", tags=["Chats"])
def rename_chat(chat_id: str, body: RenameChat, user: User, services: Services):
    try:
        return services.accounts.rename_chat(user["id"], chat_id, body.title)
    except AccountError as exc:
        return _fail(exc)


@router.delete("/chats/{chat_id}", tags=["Chats"])
def delete_chat(chat_id: str, user: User, services: Services):
    try:
        services.accounts.delete_chat(user["id"], chat_id)
    except AccountError as exc:
        return _fail(exc)
    return {"ok": True}
