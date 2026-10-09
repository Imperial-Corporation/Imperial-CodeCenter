from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from codecenter_engine import healt

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

# L'application doit être créée AVANT les routes.
app = FastAPI(title="Imperial CodeCenter")

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "rust": healt()}


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Le serveur maintient la connexion ouverte.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
