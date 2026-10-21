# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Panneau d'administration (1.0.4) : comptes, inscriptions, index, ressources, sauvegardes, métriques.

Mêmes garanties que le reste de l'administration : jeton `CODECENTER_ADMIN_TOKEN` ou compte
administrateur (le premier créé), cookie de session uniquement (jamais un jeton d'API), requêtes
inter-sites refusées.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import backup, sysinfo
from .about import VERSION
from .accounts import AccountError
from .api import Admin, Services
from .services import AIServices, data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", dependencies=[Admin], tags=["Admin"])
public_router = APIRouter(tags=["System"])

MAX_EXCLUDE_LINES = 200
MAX_EXCLUDE_LEN = 200


def _accounts(services: AIServices):
    if services.accounts is None:
        raise HTTPException(503, "Les comptes ne sont pas disponibles sur cette instance.")
    return services.accounts


def _fail(exc: AccountError) -> JSONResponse:
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)


def exclude_file() -> Path:
    configured = os.environ.get("CODECENTER_EXCLUDE_FILE", "").strip()
    return Path(configured) if configured else data_dir() / "exclude.txt"


def backups_dir() -> Path:
    return data_dir() / backup.BACKUP_DIR_NAME


# --------------------------------------------------------------------------
# Comptes et inscriptions
# --------------------------------------------------------------------------

@router.get("/users")
def users(services: Services):
    store = _accounts(services)
    return {"users": store.list_users(), "registration_open": store.registration_open}


class RegistrationIn(BaseModel):
    open: bool


@router.put("/registration")
def registration(body: RegistrationIn, services: Services):
    """Ouvre ou ferme les inscriptions (prioritaire sur `CODECENTER_REGISTRATION`, conservé au redémarrage)."""
    store = _accounts(services)
    store.registration_open = body.open
    return {"registration_open": store.registration_open}


class DisableIn(BaseModel):
    disabled: bool


@router.post("/users/{user_id}/disabled")
def disable_user(user_id: str, body: DisableIn, services: Services):
    try:
        _accounts(services).set_disabled(user_id, body.disabled)
    except AccountError as exc:
        return _fail(exc)
    return {"ok": True, "disabled": body.disabled}


class ResetIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/users/{user_id}/password")
async def reset_password(user_id: str, body: ResetIn, services: Services):
    """Fixe un nouveau mot de passe. Les clés IA chiffrées du compte sont perdues (à ressaisir) ; ses discussions restent."""
    try:
        await asyncio.to_thread(_accounts(services).admin_reset_password, user_id, body.password)
    except AccountError as exc:
        return _fail(exc)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, services: Services):
    try:
        _accounts(services).admin_delete_user(user_id)
    except AccountError as exc:
        return _fail(exc)
    return {"ok": True}


# --------------------------------------------------------------------------
# Index : progression, rescan, exclusions
# --------------------------------------------------------------------------

@router.get("/index")
async def index_status(services: Services):
    """Progression du passage d'indexation + statistiques de l'index."""
    engine = services.engine
    scan = await engine.scan_status() if hasattr(engine, "scan_status") else {"scan": {}, "generation": 0}
    try:
        from codecenter_engine import get_stats

        stats = json.loads(await asyncio.to_thread(get_stats))
    except Exception:
        stats = {}
    return {"scan": scan.get("scan", {}), "generation": scan.get("generation", 0), "stats": stats}


@router.post("/index/rescan")
def index_rescan(services: Services):
    if not hasattr(services.engine, "rescan"):
        raise HTTPException(501, "Ce moteur ne permet pas le rescan manuel.")
    services.engine.rescan()
    return {"ok": True}


class ExcludeIn(BaseModel):
    patterns: list[str] = Field(default_factory=list, max_length=MAX_EXCLUDE_LINES)


@router.get("/index/exclusions")
def get_exclusions():
    path = exclude_file()
    try:
        patterns = [p.strip() for p in path.read_text(encoding="utf-8").splitlines() if p.strip()]
    except OSError:
        patterns = []
    return {"patterns": patterns, "env": os.environ.get("CODECENTER_EXCLUDE", ""), "file": str(path)}


@router.put("/index/exclusions")
def put_exclusions(body: ExcludeIn, services: Services):
    """Écrit les motifs d'exclusion et lance un passage : le moteur relit la liste, retire de l'index ce qui
    devient exclu et ré-indexe ce qui ne l'est plus."""
    cleaned: list[str] = []
    for raw in body.patterns:
        pattern = " ".join(str(raw).split())
        if not pattern or len(pattern) > MAX_EXCLUDE_LEN or "\x00" in pattern:
            continue
        cleaned.append(pattern)
    path = exclude_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise HTTPException(503, f"Écriture impossible : {exc.strerror or exc}") from exc
    if hasattr(services.engine, "rescan"):
        services.engine.rescan()
    return {"patterns": cleaned}


# --------------------------------------------------------------------------
# Ressources
# --------------------------------------------------------------------------

def _codes_dir(services: AIServices) -> Path | None:
    return services.library.root if services.library else None


@router.get("/system")
def system(request: Request, services: Services):
    from .profile import resolve_name

    profile = getattr(request.app.state, "profile", None) or {"profile": resolve_name(data_dir())}
    return sysinfo.snapshot(data_dir(), _codes_dir(services), profile)


# --------------------------------------------------------------------------
# Sauvegardes
# --------------------------------------------------------------------------

class BackupIn(BaseModel):
    with_codes: bool = False
    passphrase: str | None = Field(default=None, max_length=256)


def _secret_key() -> Path:
    from .services import ROOT

    key_file = os.environ.get("CODECENTER_SECRET_KEY_FILE", "").strip()
    return Path(key_file) if key_file else Path(os.environ.get("CODECENTER_KEY_DIR", str(ROOT / ".local"))) / "secret.key"


def run_backup(services: AIServices, *, with_codes: bool, passphrase: str | None) -> Path:
    path = backup.create_backup(
        data_dir(), backups_dir(), accounts=services.accounts,
        codes_dir=_codes_dir(services) if with_codes else None, secret_key=_secret_key(), passphrase=passphrase)
    try:
        keep = int(os.environ.get("CODECENTER_BACKUP_KEEP", "7"))
    except ValueError:
        keep = 7
    backup.prune(backups_dir(), keep)
    return path


@router.get("/backups")
def backups():
    return {"backups": backup.list_backups(backups_dir()), "keep": os.environ.get("CODECENTER_BACKUP_KEEP", "7"),
            "every_hours": os.environ.get("CODECENTER_BACKUP_EVERY_HOURS", "0")}


@router.post("/backups")
async def create_backup(body: BackupIn, services: Services):
    if body.passphrase is not None and body.passphrase and len(body.passphrase) < 8:
        raise HTTPException(422, "Phrase secrète trop courte (8 caractères minimum).")
    try:
        path = await asyncio.to_thread(run_backup, services, with_codes=body.with_codes,
                                       passphrase=body.passphrase or None)
    except (OSError, backup.BackupError) as exc:
        logger.warning("Sauvegarde impossible : %s", exc)
        raise HTTPException(503, f"Sauvegarde impossible : {exc}") from exc
    return {"name": path.name, "size": path.stat().st_size}


def _backup_path(name: str) -> Path:
    if "/" in name or "\\" in name or ".." in name or not name.startswith("icc-backup-"):
        raise HTTPException(404, "Sauvegarde introuvable.")
    path = backups_dir() / name
    if not path.is_file():
        raise HTTPException(404, "Sauvegarde introuvable.")
    return path


@router.get("/backups/{name}")
def download_backup(name: str):
    path = _backup_path(name)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.delete("/backups/{name}")
def delete_backup(name: str):
    _backup_path(name).unlink()
    return {"ok": True}


async def backup_loop(services: AIServices) -> None:
    """Sauvegarde planifiée (`CODECENTER_BACKUP_EVERY_HOURS`, 0 = désactivée ; clair sauf `CODECENTER_BACKUP_PASSPHRASE`)."""
    try:
        hours = float(os.environ.get("CODECENTER_BACKUP_EVERY_HOURS", "0") or 0)
    except ValueError:
        hours = 0
    if hours <= 0:
        return
    passphrase = os.environ.get("CODECENTER_BACKUP_PASSPHRASE") or None
    with_codes = os.environ.get("CODECENTER_BACKUP_CODES", "").strip().lower() in {"1", "true", "yes", "oui", "on"}
    interval = max(hours, 0.1) * 3600
    while True:
        existing = backup.list_backups(backups_dir())
        newest = existing[0]["created"] if existing else 0
        import time

        wait = max(60.0, interval - (time.time() - newest)) if existing else 300.0
        await asyncio.sleep(wait)
        try:
            path = await asyncio.to_thread(run_backup, services, with_codes=with_codes, passphrase=passphrase)
            logger.info("Sauvegarde planifiée : %s", path.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Échec de la sauvegarde planifiée")


# --------------------------------------------------------------------------
# Métriques Prometheus
# --------------------------------------------------------------------------

def _metrics_allowed(request: Request, services: AIServices) -> bool:
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    for name in ("CODECENTER_METRICS_TOKEN", "CODECENTER_ADMIN_TOKEN"):
        expected = os.environ.get(name, "").strip()
        if expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode()):
            return True
    from .api import user_from_request

    user = user_from_request(request, services, allow_token=False)
    return bool(user and services.accounts and services.accounts.is_admin(user["id"]))


@public_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, services: Services):
    """Métriques Prometheus. Accès : jeton `CODECENTER_METRICS_TOKEN` / `CODECENTER_ADMIN_TOKEN` (en-tête Bearer)
    ou administrateur connecté ; `CODECENTER_METRICS_PUBLIC=1` les ouvre sans authentification (réseau privé)."""
    public = os.environ.get("CODECENTER_METRICS_PUBLIC", "").strip().lower() in {"1", "true", "yes", "oui", "on"}
    if not public and not _metrics_allowed(request, services):
        raise HTTPException(401, "Accès aux métriques refusé.", headers={"WWW-Authenticate": "Bearer"})
    engine = services.engine
    scan = await engine.scan_status() if hasattr(engine, "scan_status") else {}
    try:
        from codecenter_engine import get_stats

        stats = json.loads(await asyncio.to_thread(get_stats))
    except Exception:
        stats = {}
    system = sysinfo.snapshot(data_dir(), _codes_dir(services))
    users = services.accounts.user_count() if services.accounts else None
    return PlainTextResponse(
        sysinfo.prometheus(stats=stats, scan=scan, system=system, users=users, version=VERSION),
        media_type="text/plain; version=0.0.4; charset=utf-8")
