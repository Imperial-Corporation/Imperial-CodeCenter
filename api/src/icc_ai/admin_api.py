"""Routes d'administration : import de fichiers, crawler, santé de l'index.

Toutes exigent les mêmes garanties que la configuration IA (jeton
d'administration si `CODECENTER_ADMIN_TOKEN` est défini, refus des requêtes
inter-sites).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError

from .api import Admin, Services
from .crawl_jobs import CrawlRequest, JobBusyError
from .errors import ConfigError, EngineError
from .library import MAX_FILES_PER_UPLOAD, DEPOT_NAME, LibraryError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Admin])


# --------------------------------------------------------------------------
# Bibliothèque : import avec tags et zone de dépôt
# --------------------------------------------------------------------------

def _library(services: Services):
    if services.library is None:
        raise HTTPException(503, "La bibliothèque n'est pas initialisée.")
    return services.library


@router.get("/library/info", tags=["Library"])
def library_info(services: Services):
    """Dossier surveillé, zone de dépôt, limites et extensions acceptées."""
    library = _library(services)
    limits = library.limits
    return {
        "root": str(library.root),
        "depot": str(library.depot),
        "depot_name": DEPOT_NAME,
        "extensions": library.supported_extensions(),
        "max_file_kb": limits.max_file_bytes // 1000,
        "max_upload_mb": limits.max_upload_bytes // 1_000_000,
        "max_files": MAX_FILES_PER_UPLOAD,
        "min_free_gb": round(limits.min_free_bytes / 1024**3, 2),
    }


@router.post("/library/import", tags=["Library"])
async def library_import(
    services: Services,
    files: Annotated[list[UploadFile], File(description="Fichiers de code ou archives ZIP")],
    tags: Annotated[str, Form(max_length=400)] = "",
    auto_classify: Annotated[bool, Form()] = True,
    keep_folders: Annotated[bool, Form()] = True,
):
    """Range les fichiers envoyés selon les tags (dossiers imbriqués tag1/tag2/<langage>)."""
    library = _library(services)
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(413, f"Trop de fichiers (max {MAX_FILES_PER_UPLOAD}).")

    payload: list[tuple[str, bytes]] = []
    remaining = library.limits.max_upload_bytes
    for upload in files:
        # Lecture bornée : on ne charge jamais plus que le plafond restant.
        cap = min(remaining, max(library.limits.max_file_bytes, 20_000_000)
                  if (upload.filename or "").lower().endswith(".zip") else library.limits.max_file_bytes)
        data = await upload.read(cap + 1)
        if len(data) > cap:
            if (upload.filename or "").lower().endswith(".zip") or remaining <= library.limits.max_file_bytes:
                raise HTTPException(413, "Envoi trop volumineux.")
            payload.append((upload.filename or "", data[: cap + 1]))  # refusé plus tard (trop gros)
            remaining -= len(data)
            continue
        remaining -= len(data)
        payload.append((upload.filename or "", data))

    try:
        report = await asyncio.to_thread(
            library.import_upload, payload, tags, auto_classify=auto_classify, keep_folders=keep_folders)
    except LibraryError as exc:
        raise HTTPException(413 if exc.code.endswith("too_large") else 422, exc.message) from exc
    except OSError as exc:
        logger.warning("Import impossible : %s", exc)
        raise HTTPException(
            503, "Écriture impossible dans le dossier surveillé : vérifie ses permissions (docs/permissions.md).") from exc
    return report.as_dict()


@router.post("/library/depot/scan", tags=["Library"])
async def library_scan_depot(services: Services):
    """Range tout de suite le contenu de la zone de dépôt."""
    library = _library(services)
    report = await asyncio.to_thread(library.scan_depot, settle_seconds=0.0)
    return report.as_dict()


# --------------------------------------------------------------------------
# Santé de l'indexation
# --------------------------------------------------------------------------

@router.get("/index/health", tags=["Library"])
async def index_health(services: Services):
    """Fichiers en échec, dossiers non inscriptibles, fichiers ignorés + espace disque."""
    try:
        report = await services.engine.index_health()
    except EngineError as exc:
        raise HTTPException(503, exc.message) from exc
    except AttributeError:
        report = {"failures": [], "read_only_directories": [], "skipped": [], "indexed_in_session": 0}
    disk = services.crawler.disk_info() if services.crawler else {}
    return {**report, "disk": disk}


# --------------------------------------------------------------------------
# Crawler GitHub
# --------------------------------------------------------------------------

def _crawler(services: Services):
    if services.crawler is None:
        raise HTTPException(503, "Le crawler n'est pas initialisé.")
    return services.crawler


class TokenIn(BaseModel):
    token: str | None = Field(default=None, max_length=300)
    clear: bool = False


@router.get("/crawler", tags=["Crawler"])
def crawler_status(services: Services):
    return _crawler(services).status()


@router.post("/crawler/start", tags=["Crawler"])
async def crawler_start(body: CrawlRequest, services: Services):
    """Lance (ou planifie) une collecte avec ses objectifs d'arrêt.

    `async` : la tâche de fond doit être créée dans la boucle d'événements
    (une route synchrone tourne dans un thread sans boucle).
    """
    manager = _crawler(services)
    try:
        return manager.start(body)
    except ValueError as exc:
        raise HTTPException(409 if isinstance(exc, JobBusyError) else 422, str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/crawler/stop", tags=["Crawler"])
async def crawler_stop(services: Services):
    job = await _crawler(services).stop()
    return job or {}


@router.put("/crawler/token", tags=["Crawler"])
def crawler_token(body: TokenIn, services: Services):
    """Enregistre (chiffré) ou efface le jeton GitHub ; il n'est jamais renvoyé."""
    manager = _crawler(services)
    try:
        manager.save_token(body.token, clear=body.clear)
    except (ValueError, ConfigError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"token": manager.token_source()}
