# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from codecenter_engine import get_stats, healt, start_watcher, stop_watcher

from icc_ai.about import ATTRIBUTION, LICENSE_NAME, VERSION, WEBSITE, about
from icc_ai.accounts_api import router as accounts_router
from icc_ai.admin_api import router as admin_router
from icc_ai.api import router as ai_router
from icc_ai.proxy import ProxyMiddleware, ProxySettings
from icc_ai.services import build_services


# --------------------------------------------------
# Configuration
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Cycle de vie
# --------------------------------------------------

async def _depot_loop(library) -> None:
    """Range périodiquement la zone de dépôt (CODECENTER_DEPOT_SECS, 0 = désactivé)."""
    try:
        interval = float(os.environ.get("CODECENTER_DEPOT_SECS", "15") or 15)
    except ValueError:
        interval = 15.0
    if interval <= 0:
        return
    while True:
        try:
            report = await asyncio.to_thread(library.scan_depot)
            if report.saved or report.skipped:
                logger.info("Zone de dépôt : %d rangé(s), %d ignoré(s)", len(report.saved), len(report.skipped))
                for item in report.skipped:
                    logger.warning("Dépôt ignoré : %s (%s)", item["name"], item["reason"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Échec du passage sur la zone de dépôt")
        await asyncio.sleep(max(interval, 2.0))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarre et arrête le watcher avec l'application."""
    logger.info("Démarrage du watcher CodeCenter")
    start_watcher()

    # Services IA : configuration chiffrée, moteur Rust, orchestrateur.
    # Un échec (clé de chiffrement invalide…) ne doit pas empêcher le
    # démarrage du reste de l'application : les routes IA répondront 503.
    try:
        app.state.ai = build_services()
    except Exception:
        app.state.ai = None
        logger.exception("Impossible d'initialiser les services IA")

    depot_task = None
    services = app.state.ai
    if services is not None and services.library is not None:
        services.library.ensure_depot()
        depot_task = asyncio.create_task(_depot_loop(services.library))
    if services is not None and services.crawler is not None:
        services.crawler.resume()

    try:
        yield
    finally:
        if depot_task is not None:
            depot_task.cancel()
            await asyncio.gather(depot_task, return_exceptions=True)
        if services is not None and services.crawler is not None:
            await services.crawler.shutdown()
        logger.info("Arrêt du watcher CodeCenter")
        try:
            stop_watcher()
        except Exception:
            logger.exception("Échec de l'arrêt du watcher")


# --------------------------------------------------
# Application
# --------------------------------------------------

app = FastAPI(
    title="Imperial CodeCenter",
    version=VERSION,
    description=f"Moteur de recherche de code assisté par IA. {ATTRIBUTION}.",
    contact={"name": "Imperial", "url": WEBSITE},
    license_info={"name": LICENSE_NAME, "url": WEBSITE},
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


# --------------------------------------------------
# IA : configuration des fournisseurs et recherche (/api/v1/search)
# --------------------------------------------------

@app.get("/LICENSE", include_in_schema=False)
@app.get("/NOTICE", include_in_schema=False)
def legal_text(request: Request):
    """Texte de la licence et des mentions (à conserver avec le logiciel)."""
    name = request.url.path.strip("/")
    path = ROOT / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@app.get("/api/v1/about", tags=["System"])
async def about_route():
    """Nom, version, origine (Imperial), licence et crédits."""
    return about()


app.include_router(ai_router)
app.include_router(admin_router)
app.include_router(accounts_router)

# Proxy inverse (nginx / Cloudflare) : inactif sauf si CODECENTER_PROXY_MODE le demande.
# Les en-têtes de sécurité sont envoyés dans tous les cas.
app.add_middleware(ProxyMiddleware)


# --------------------------------------------------
# Santé et statistiques
# --------------------------------------------------

@app.get("/api/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "rust": healt(),
    }


@app.get("/api/v1/status", tags=["System"])
def status():
    return {
        "api": "available",
        "engine": "loaded",
        "message": healt(),
    }


@app.get("/api/v1/proxy/check", tags=["System"])
def proxy_check(request: Request):
    """Aide au réglage du proxy : ce que le service voit réellement de la requête."""
    settings = ProxySettings.from_environment()
    return {
        "mode": settings.mode,
        "client_ip": request.client.host if request.client else None,
        "scheme": request.url.scheme,
        "host": request.headers.get("host"),
        "forwarded_headers_present": [
            h for h in ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip",
                        "cf-connecting-ip", "cf-visitor") if h in request.headers
        ],
    }


@app.get("/api/v1/stats", tags=["System"])
def statistics():
    try:
        return json.loads(get_stats())
    except Exception as exc:
        logger.exception("Échec de récupération des statistiques")
        raise HTTPException(
            status_code=503,
            detail="Impossible de récupérer les statistiques du moteur.",
        ) from exc


# --------------------------------------------------
# Frontend
# --------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")


# --------------------------------------------------
# WebSocket
# --------------------------------------------------

@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
