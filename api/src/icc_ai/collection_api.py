# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Onglet Bibliothèque de l'administration : fichiers collectés depuis GitHub.

Consulter, rechercher, modifier (localement), reclasser, télécharger et supprimer les fichiers
téléchargés par le crawler. Rien n'est jamais renvoyé vers GitHub : les modifications restent
dans le dossier surveillé (avec historique restaurable). Routes réservées à l'administrateur.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .crawler_bridge import ensure_importable

ensure_importable()  # `icc_crawler` vit dans crawler/src, hors du chemin d'import de l'API (image Docker comprise)

from icc_crawler.catalog import MAX_PAGE_SIZE, Catalog, CatalogError

from . import library_ai
from .api import Admin, Services, UserConfig
from .errors import ConfigError, ProviderError
from .providers.registry import create_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/library", dependencies=[Admin], tags=["Library"])

T = TypeVar("T")

_STATUS = {"not_found": 404, "missing": 404, "deleted": 410, "conflict": 409, "too_large": 413,
           "binary": 422, "invalid": 422, "disabled": 403, "quota": 429}


def _destination(services) -> Path:
    if services.crawler is None:
        raise HTTPException(503, "Le crawler n'est pas initialisé.")
    return services.crawler.destination


async def _with_catalog(services, action: Callable[[Catalog], T], *, create: bool = False) -> T | None:
    """Exécute `action` sur le catalogue, dans un thread (SQLite et disque sont bloquants).

    Sans dossier de collecte (rien n'a encore été téléchargé), les lectures renvoient None :
    on ne crée pas le dossier juste pour afficher une liste vide.
    """
    destination = _destination(services)
    if not create and not (destination / ".crawler" / "crawler.sqlite3").is_file():
        return None

    def run() -> T:
        catalog = Catalog(destination)
        try:
            return action(catalog)
        finally:
            catalog.close()

    try:
        return await asyncio.to_thread(run)
    except CatalogError as exc:
        raise HTTPException(_STATUS.get(exc.code, 400), exc.message) from exc
    except OSError as exc:
        logger.warning("Bibliothèque : erreur disque : %s", exc)
        raise HTTPException(503, "Accès au dossier de la bibliothèque impossible (permissions ?).") from exc


EMPTY_PAGE = {"items": [], "total": 0, "page": 1, "page_size": 50, "partial": False}
EMPTY_FACETS = {"categories": [], "languages": [], "licenses": [], "repos": [], "files": 0, "bytes": 0}


@router.get("/files")
async def list_files(
    services: Services,
    q: Annotated[str, Query(max_length=200)] = "",
    scope: Annotated[str, Query(pattern="^(name|content|all)$")] = "name",
    repo: Annotated[str, Query(max_length=200)] = "",
    language: Annotated[str, Query(max_length=40)] = "",
    category: Annotated[str, Query(max_length=60)] = "",
    subcategory: Annotated[str, Query(max_length=60)] = "",
    license: Annotated[str, Query(max_length=60)] = "",
    extension: Annotated[str, Query(max_length=16)] = "",
    status: Annotated[str, Query(pattern="^(active|deleted)$")] = "active",
    modified: bool = False,
    sort: Annotated[str, Query(max_length=20)] = "collected_at",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
):
    """Fichiers collectés : recherche par nom (et/ou contenu), filtres, tri et pagination."""
    result = await _with_catalog(services, lambda c: c.list_files(
        q=q, scope=scope, repo=repo, language=language, category=category, subcategory=subcategory,
        license=license, extension=extension, status=status, modified=modified, sort=sort, order=order,
        page=page, page_size=page_size).as_dict())
    return result or {**EMPTY_PAGE, "page": page, "page_size": page_size}


@router.get("/files/facets")
async def facets(services: Services):
    """Valeurs disponibles pour les filtres (catégories avec sous-catégories, langages, licences, dépôts)."""
    return await _with_catalog(services, lambda c: c.facets()) or EMPTY_FACETS


@router.get("/files/{file_id}")
async def file_detail(file_id: int, services: Services):
    return await _with_catalog(services, lambda c: c.get(file_id)) or _not_found()


def _not_found():
    raise HTTPException(404, "Aucun fichier collecté.")


@router.get("/files/{file_id}/content")
async def file_content(
    file_id: int, services: Services,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1)] = None,
):
    """Contenu complet, ou tranche d'octets (`offset`/`limit`) pour les fichiers volumineux."""
    return await _with_catalog(services, lambda c: c.read(file_id, offset=offset, limit=limit)) or _not_found()


class ContentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=4_000_000)
    expected_sha256: str = Field(pattern="^[0-9a-f]{64}$")


@router.put("/files/{file_id}/content")
async def save_content(file_id: int, body: ContentIn, services: Services):
    """Enregistre une modification LOCALE (jamais envoyée sur GitHub). Ancienne version gardée."""
    return await _with_catalog(services, lambda c: c.write(file_id, body.content, body.expected_sha256)) \
        or _not_found()


@router.get("/files/{file_id}/download")
async def download(file_id: int, services: Services):
    found = await _with_catalog(services, lambda c: c.download(file_id)) or _not_found()
    path, name = found
    # Toujours en pièce jointe : un fichier collecté (HTML, SVG…) ne doit jamais s'exécuter dans le site.
    return FileResponse(path, filename=name, media_type="text/plain; charset=utf-8",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/files/{file_id}")
async def delete_file(file_id: int, services: Services):
    """Supprime le fichier local (une collecte suivante ne le retélécharge pas, sauf collecte forcée)."""
    def run(catalog: Catalog) -> bool:
        catalog.delete(file_id)
        return True

    await _with_catalog(services, run) or _not_found()
    return {"deleted": file_id}


@router.get("/files/{file_id}/versions")
async def versions(file_id: int, services: Services):
    return {"versions": await _with_catalog(services, lambda c: c.versions(file_id)) or []}


@router.get("/files/{file_id}/versions/{version_id}")
async def version_content(file_id: int, version_id: int, services: Services):
    return await _with_catalog(services, lambda c: c.version_content(file_id, version_id)) or _not_found()


@router.post("/files/{file_id}/versions/{version_id}/restore")
async def restore_version(file_id: int, version_id: int, services: Services):
    return await _with_catalog(services, lambda c: c.restore(file_id, version_id)) or _not_found()


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=60)
    subcategory: str = Field(default="", max_length=60)
    tags: list[str] | None = Field(default=None, max_length=10)


@router.put("/files/{file_id}/category")
async def set_category(file_id: int, body: CategoryIn, services: Services):
    """Reclasse un fichier à la main : il est déplacé dans le dossier de sa catégorie."""
    return await _with_catalog(
        services, lambda c: c.set_category(file_id, body.category, body.subcategory, tags=body.tags)
    ) or _not_found()


# -- analyse IA plafonnée -----------------------------------------------------------------------------


@router.get("/ai-tagging")
async def ai_tagging_status(services: Services):
    """Plafonds et consommation du jour de l'analyse IA (aucun appel au fournisseur)."""
    data_dir = services.crawler.data_dir if services.crawler else Path(".")
    found = await _with_catalog(services, lambda c: library_ai.status(data_dir, c))
    return found or {"enabled": library_ai.daily_max() > 0, "daily_max": library_ai.daily_max(),
                     "used_today": 0, "remaining_today": library_ai.daily_max(),
                     "max_batch": library_ai.MAX_BATCH, "max_chars": library_ai.MAX_CHARS, "candidates": 0}


class AiTagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] | None = Field(default=None, max_length=library_ai.MAX_BATCH)
    limit: int = Field(default=10, ge=1, le=library_ai.MAX_BATCH)


@router.post("/ai-tagging")
async def ai_tagging(body: AiTagIn, services: Services, config: UserConfig):
    """Analyse par l'IA quelques fichiers non classés (fournisseur actif du compte connecté)."""
    try:
        runtime = config.active_runtime()
    except ConfigError as exc:
        raise HTTPException(409, str(exc)) from exc
    if runtime is None:
        raise HTTPException(409, "Aucun fournisseur IA actif : configure-en un dans l'onglet IA.")
    provider = create_provider(runtime)
    destination = _destination(services)
    data_dir = services.crawler.data_dir

    if not (destination / ".crawler" / "crawler.sqlite3").is_file():
        raise HTTPException(404, "Aucun fichier collecté : lance d'abord une collecte.")
    # Créé ici (fil de la boucle) : une connexion SQLite ne passe pas d'un fil à l'autre.
    catalog = Catalog(destination)
    try:
        # `analyse` est asynchrone (appels réseau) ; les accès SQLite restent courts et sur ce fil.
        return await library_ai.analyse(provider, catalog, data_dir, ids=body.ids, limit=body.limit)
    except CatalogError as exc:
        raise HTTPException(_STATUS.get(exc.code, 400), exc.message) from exc
    except ProviderError as exc:
        raise HTTPException(502, exc.message) from exc
    finally:
        catalog.close()
