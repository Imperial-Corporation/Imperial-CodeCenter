"""Routes d'administration : import, dépôt, santé de l'index, crawler, protection."""

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine, FakeProvider, turn
from icc_ai import admin_api, api
from icc_ai.crawl_jobs import CrawlManager
from icc_ai.library import Library, Limits
from icc_ai.service import SearchService
from icc_ai.services import AIServices

CODE = b'def hello():\n    """Dit bonjour."""\n    return "salut"\n'


@pytest.fixture
def app(tmp_path, store, secret_box):
    engine = FakeEngine()
    root = tmp_path / "codes"
    root.mkdir()
    library = Library(root, tmp_path / "data", Limits(max_file_bytes=50_000, max_upload_bytes=1_000_000, min_free_bytes=0))
    crawler = CrawlManager(tmp_path / "data", root, secret_box)
    service = SearchService(store, engine, provider_factory=lambda r, t=None: FakeProvider([turn("{}")]))
    application = FastAPI()
    application.include_router(api.router)
    application.include_router(admin_api.router)
    application.state.ai = AIServices(store, engine, service, library=library, crawler=crawler)
    application.state.root = root
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_info_bibliotheque(client):
    data = client.get("/api/v1/library/info").json()
    assert "py" in data["extensions"] and data["max_files"] > 0


def test_import_range_selon_les_tags(client, app):
    response = client.post("/api/v1/library/import", data={"tags": "Crypto, AES chiffrement"},
                           files=[("files", ("aes.py", CODE))])
    assert response.status_code == 200, response.text
    assert (app.state.root / "crypto" / "aes-chiffrement" / "python" / "aes.py").is_file()


def test_import_extension_non_supportee_ignoree(client):
    response = client.post("/api/v1/library/import", data={"tags": ""},
                           files=[("files", ("notes.txt", b"bonjour"))])
    assert response.status_code == 200
    body = response.json()
    assert not body.get("imported")


def test_import_zip_slip_refuse(client, app, tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../evil.py", CODE)
        archive.writestr("ok/bon.py", CODE)
    response = client.post("/api/v1/library/import", data={"tags": "zip"},
                           files=[("files", ("a.zip", buffer.getvalue()))])
    assert response.status_code == 200
    assert not (tmp_path / "evil.py").exists()
    assert not list(app.state.root.rglob("evil.py"))


def test_import_trop_gros_refuse(client):
    big = b"x = 1\n" * 20_000
    response = client.post("/api/v1/library/import", files=[("files", ("gros.py", big))])
    assert response.status_code in (200, 413)
    if response.status_code == 200:
        assert not response.json().get("imported")


def test_scan_depot(client, app):
    depot = app.state.ai.library.depot
    app.state.ai.library.ensure_depot()
    (depot / "tags.txt").write_text("reseau, debug\n")
    (depot / "ping.py").write_bytes(CODE)
    response = client.post("/api/v1/library/depot/scan")
    assert response.status_code == 200
    assert (app.state.root / "reseau" / "debug" / "python" / "ping.py").is_file()


def test_sante_index_sans_methode_moteur(client):
    data = client.get("/api/v1/index/health").json()
    assert data["failures"] == [] and "disk" in data


def test_crawler_statut_et_validation(client):
    status = client.get("/api/v1/crawler").json()
    assert status["active"] is False and "disk" in status
    assert client.post("/api/v1/crawler/start", json={"languages": ["klingon"]}).status_code == 422
    assert client.post("/api/v1/crawler/start", json={"min_free_gb": 0}).status_code == 422


def test_crawler_planifie_puis_annule(client):
    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    started = client.post("/api/v1/crawler/start", json={"languages": ["python"], "start_at": later, "max_minutes": 5})
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "scheduled"
    assert client.post("/api/v1/crawler/start", json={"languages": ["python"]}).status_code == 409
    stopped = client.post("/api/v1/crawler/stop").json()
    assert stopped["state"] == "cancelled"


def test_jeton_github_jamais_renvoye(client):
    secret = "ghp_" + "a" * 36
    response = client.put("/api/v1/crawler/token", json={"token": secret})
    assert response.status_code == 200 and secret not in response.text
    assert secret not in json.dumps(client.get("/api/v1/crawler").json())
    assert client.put("/api/v1/crawler/token", json={"clear": True}).status_code == 200


def test_protection_jeton_admin(client, monkeypatch):
    monkeypatch.setenv("CODECENTER_ADMIN_TOKEN", "secret-admin")
    assert client.get("/api/v1/crawler").status_code == 401
    assert client.get("/api/v1/crawler", headers={"Authorization": "Bearer faux"}).status_code == 401
    assert client.get("/api/v1/crawler", headers={"Authorization": "Bearer secret-admin"}).status_code == 200
    assert client.post("/api/v1/library/depot/scan").status_code == 401


def test_requete_inter_sites_refusee(client):
    response = client.post("/api/v1/library/depot/scan", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_services_absents_503(tmp_path, store):
    engine = FakeEngine()
    service = SearchService(store, engine, provider_factory=lambda r, t=None: None)
    application = FastAPI()
    application.include_router(admin_api.router)
    application.state.ai = AIServices(store, engine, service)
    bare = TestClient(application)
    assert bare.get("/api/v1/library/info").status_code == 503
    assert bare.get("/api/v1/crawler").status_code == 503
