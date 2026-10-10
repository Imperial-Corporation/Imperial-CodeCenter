import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from codecenter_engine import (
    get_stats,
    healt,
    start_watcher,
    stop_watcher,
)

# --------------------------------------------------
# Configuration
# --------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Cycle de vie de l'application
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarre et arrête les services du moteur avec l'API."""
    logger.info("Démarrage du watcher CodeCenter")

    start_watcher()

    try:
        yield
    finally:
        logger.info("Arrêt du watcher CodeCenter")

        try:
            stop_watcher()
        except Exception:
            logger.exception("Échec de l'arrêt du watcher")


app = FastAPI(
    title="Imperial CodeCenter API",
    version="0.1.0",
    description="API locale du moteur Imperial CodeCenter.",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=FRONTEND),
    name="static",
)


# --------------------------------------------------
# Schémas des requêtes
# --------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=100)
    language: str | None = Field(default=None, max_length=50)


# --------------------------------------------------
# Fonctions métier — implémentation à venir
# --------------------------------------------------

def search_documents(
    query: str,
    limit: int = 10,
    language: str | None = None,
) -> dict:
    """Recherche des documents via le moteur Rust."""
    raise NotImplementedError


async def import_documents(
    files: list[UploadFile],
) -> dict:
    """Valide, stocke et indexe les fichiers importés."""
    raise NotImplementedError


def list_documents(
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Retourne une page de documents indexés."""
    raise NotImplementedError


def get_document(
    document_id: str,
) -> dict:
    """Récupère les métadonnées d'un document."""
    raise NotImplementedError


def delete_document(
    document_id: str,
) -> dict:
    """Supprime un document et son entrée d'index."""
    raise NotImplementedError


def rebuild_index() -> dict:
    """Reconstruit l'index à partir des documents stockés."""
    raise NotImplementedError


def get_statistics() -> dict:
    """Retourne les statistiques du moteur et de l'index."""
    try:
        return json.loads(get_stats())
    except Exception as exc:
        logger.exception(
            "Échec de récupération des statistiques Rust/PostgreSQL"
        )
        raise HTTPException(
            status_code=503,
            detail="Impossible de récupérer les statistiques du moteur.",
        ) from exc


def get_configuration() -> dict:
    """Retourne la configuration publique de CodeCenter."""
    raise NotImplementedError


# --------------------------------------------------
# Gestion des fonctionnalités non implémentées
# --------------------------------------------------

def not_implemented(feature: str):
    raise HTTPException(
        status_code=501,
        detail=f"Fonctionnalité non implémentée : {feature}",
    )


# --------------------------------------------------
# Frontend
# --------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")


# --------------------------------------------------
# Santé et statut
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


@app.get("/api/v1/stats", tags=["System"])
def statistics():
    return get_statistics()


# --------------------------------------------------
# Recherche
# --------------------------------------------------

@app.post("/api/v1/search", tags=["Search"])
def search(request: SearchRequest):
    return not_implemented("search")


# --------------------------------------------------
# Documents
# --------------------------------------------------

@app.post("/api/v1/documents", tags=["Documents"])
async def upload_documents(
    files: list[UploadFile] = File(...),
):
    return not_implemented("document import")


@app.get("/api/v1/documents", tags=["Documents"])
def documents(
    limit: int = 50,
    offset: int = 0,
):
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=422,
            detail="limit doit être compris entre 1 et 100.",
        )

    if offset < 0:
        raise HTTPException(
            status_code=422,
            detail="offset doit être positif ou nul.",
        )

    return not_implemented("document listing")


@app.get("/api/v1/documents/{document_id}", tags=["Documents"])
def document_details(document_id: str):
    return not_implemented("document details")


@app.delete("/api/v1/documents/{document_id}", tags=["Documents"])
def remove_document(document_id: str):
    return not_implemented("document deletion")


# --------------------------------------------------
# Index
# --------------------------------------------------

@app.post("/api/v1/index/rebuild", tags=["Index"])
def index_rebuild():
    return not_implemented("index rebuild")


# --------------------------------------------------
# Configuration
# --------------------------------------------------

@app.get("/api/v1/config", tags=["System"])
def configuration():
    return not_implemented("configuration")


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