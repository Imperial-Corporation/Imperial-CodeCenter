# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Routes FastAPI : configuration sans fuite de secret, recherche, erreurs."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine, FakeProvider, run, turn
from icc_ai import accounts_api, api as api_module
from icc_ai.accounts import AccountStore
from icc_ai.api import SearchRequest, router
from icc_ai.config.user_store import UserConfigStore
from icc_ai.errors import EngineError, ProviderError
from icc_ai.service import SearchService
from icc_ai.services import AIServices

KEY = "sk-live-abcdef123456"
ANSWER = json.dumps({"summary": "Réponse", "used_sources": ["S1"]})


@pytest.fixture
def make_app(tmp_path):
    def build(provider=None, engine=None):
        engine = engine or FakeEngine()
        service = SearchService(None, engine, provider_factory=lambda r, t=None: provider)
        app = FastAPI()
        app.include_router(router)
        app.include_router(accounts_api.router)
        accounts = AccountStore(tmp_path / "accounts.db")
        app.state.ai = AIServices(None, engine, service, accounts=accounts)
        return app

    return build


def login(client, name="alice", password="motdepasse-ok"):
    """Crée un compte et ouvre sa session (la recherche et la configuration IA l'exigent)."""
    response = client.post("/api/v1/auth/register", json={"username": name, "password": password})
    assert response.status_code == 200, response.text
    return client


def ollama(client, model="m"):
    config = {"base_url": "http://127.0.0.1:11434", "model": model, "activate": True}
    assert client.put("/api/v1/ai/providers/ollama", json=config).status_code == 200


@pytest.fixture
def client(make_app):
    return login(TestClient(make_app(FakeProvider([turn(ANSWER)]))))


SAVE = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "api_key": KEY, "activate": True}


def test_catalogue_complet_sans_secret(client):
    data = client.get("/api/v1/ai/catalog").json()
    assert [p["id"] for p in data["providers"]][:6] == ["ollama", "omniroute", "claude", "openai", "groq", "gemini"]
    assert len(data["providers"]) == 18
    assert all(p["glyph"] and p["accent"] for p in data["providers"])
    assert {f["key"] for f in data["advanced_fields"]} >= {"timeout_s", "context_window", "max_tool_calls"}


def test_enregistrement_ne_renvoie_jamais_la_cle(client, tmp_path):
    response = client.put("/api/v1/ai/providers/openai", json=SAVE)
    assert response.status_code == 200 and KEY not in response.text
    view = client.get("/api/v1/ai/config").json()
    assert KEY not in json.dumps(view)
    assert view["active"] == "openai" and view["providers"]["openai"]["api_key_set"] is True
    # Rien d'exploitable sur le disque : ni la clé en clair, ni dans la base SQLite (blocs chiffrés).
    on_disk = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY.encode() not in on_disk and b"api.openai.com" not in on_disk


def test_fournisseur_inconnu_et_donnees_invalides(client):
    assert client.put("/api/v1/ai/providers/nope", json=SAVE).status_code == 404
    assert client.put("/api/v1/ai/providers/openai", json={**SAVE, "base_url": "ftp://x"}).status_code == 422
    assert client.put("/api/v1/ai/providers/openai", json={**SAVE, "advanced": {"timeout_s": 1}}).status_code == 422
    assert client.put("/api/v1/ai/providers/openai", json={**SAVE, "advanced": {"x": 1}}).status_code == 422


def test_test_de_connexion(client, monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    real = api_module.create_provider
    monkeypatch.setattr(api_module, "create_provider",
                        lambda runtime: real(runtime, httpx.MockTransport(handler)))
    ok = client.post("/api/v1/ai/providers/openai/test", json=SAVE).json()
    assert ok["ok"] and ok["models"] == ["gpt-4o-mini"] and seen["auth"] == f"Bearer {KEY}"

    # La clé enregistrée est réutilisée sans qu'elle transite par le formulaire.
    client.put("/api/v1/ai/providers/openai", json=SAVE)
    seen.clear()
    draft = {k: v for k, v in SAVE.items() if k != "api_key"}
    assert client.post("/api/v1/ai/providers/openai/test", json=draft).json()["ok"]
    assert seen["auth"] == f"Bearer {KEY}"


def test_test_de_connexion_echec_sans_fuite(client, monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}})

    real = api_module.create_provider
    monkeypatch.setattr(api_module, "create_provider",
                        lambda runtime: real(runtime, httpx.MockTransport(handler)))
    result = client.post("/api/v1/ai/providers/openai/test", json=SAVE).json()
    assert result["ok"] is False and result["kind"] == "auth" and KEY not in json.dumps(result)


def test_configuration_exige_un_compte(make_app):
    visitor = TestClient(make_app(FakeProvider([])))
    assert visitor.get("/api/v1/ai/config").status_code == 401
    assert visitor.put("/api/v1/ai/providers/openai", json=SAVE).status_code == 401
    assert visitor.post("/api/v1/ai/providers/openai/test", json=SAVE).status_code == 401
    assert visitor.post("/api/v1/search", json={"query": "parse_json"}).status_code == 401
    assert visitor.post("/api/v1/chat", json={"conversation_id": "abcdefgh12", "message": "x"}).status_code == 401
    assert visitor.get("/api/v1/ai/catalog").status_code == 200  # le catalogue reste public


def test_cles_isolees_entre_comptes(make_app):
    app = make_app(FakeProvider([turn(ANSWER)]))
    alice = login(TestClient(app), "alice")
    alice.put("/api/v1/ai/providers/openai", json=SAVE)
    bob = login(TestClient(app), "bobby")
    view = bob.get("/api/v1/ai/config").json()
    assert view["active"] is None and view["providers"]["openai"]["api_key_set"] is False
    # Bob n'a rien configuré : pas de synthèse, et la clé d'Alice n'est jamais utilisée.
    body = bob.post("/api/v1/search", json={"query": "parse_json"}).json()
    assert body["status"] == "search_only"
    assert alice.get("/api/v1/ai/config").json()["providers"]["openai"]["api_key_set"] is True


def test_configuration_survit_a_la_reconnexion_et_au_changement_de_mot_de_passe(make_app):
    app = make_app(FakeProvider([]))
    c = login(TestClient(app))
    c.put("/api/v1/ai/providers/openai", json=SAVE)
    c.post("/api/v1/auth/logout")
    assert c.post("/api/v1/auth/login", json={"username": "alice", "password": "motdepasse-ok"}).status_code == 200
    assert c.get("/api/v1/ai/config").json()["providers"]["openai"]["api_key_set"] is True
    assert c.post("/api/v1/auth/password", json={"current": "motdepasse-ok", "new": "nouveau-mdp-ok"}).status_code == 200
    c.post("/api/v1/auth/logout")
    assert c.post("/api/v1/auth/login", json={"username": "alice", "password": "nouveau-mdp-ok"}).status_code == 200
    assert c.get("/api/v1/ai/config").json()["providers"]["openai"]["api_key_set"] is True


def test_requete_inter_sites_refusee(client):
    evil = {"Origin": "https://evil.example"}
    assert client.put("/api/v1/ai/providers/openai", json=SAVE, headers=evil).status_code == 403
    same = {"Origin": "http://testserver"}
    assert client.put("/api/v1/ai/providers/openai", json=SAVE, headers=same).status_code == 200


def test_recherche_complete(make_app):
    c = login(TestClient(make_app(FakeProvider([turn(ANSWER)]))))
    ollama(c)
    body = c.post("/api/v1/search", json={"query": "parse_json", "limit": 5}).json()
    assert body["status"] == "ok" and body["answer"]["summary"] == "Réponse"
    assert body["sources"][0]["path"] == "codes/json_tools.py" and body["sources"][0]["cited"]
    assert KEY not in json.dumps(body)


def test_recherche_sans_fournisseur(client):
    body = client.post("/api/v1/search", json={"query": "parse_json"}).json()
    assert body["status"] == "search_only" and body["answer"] is None and body["sources"]


@pytest.mark.parametrize("payload", [
    {}, {"query": ""}, {"query": "x" * 2001}, {"query": "ok", "limit": 0}, {"query": "ok", "limit": 999},
])
def test_recherche_requete_invalide(client, payload):
    assert client.post("/api/v1/search", json=payload).status_code == 422


def test_requete_blanche(client):
    assert client.post("/api/v1/search", json={"query": "   "}).status_code == 422


@pytest.mark.parametrize("error,status,code", [
    (ProviderError("timeout", "Trop long"), 504, "provider_timeout"),
    (ProviderError("rate_limit", "Limite"), 429, "provider_rate_limit"),
    (ProviderError("auth", "Clé refusée"), 502, "provider_auth"),
    (ProviderError("network", "Injoignable"), 502, "provider_network"),
])
def test_erreurs_fournisseur_traduites(make_app, error, status, code):
    c = login(TestClient(make_app(FakeProvider([error]))))
    ollama(c)
    response = c.post("/api/v1/search", json={"query": "parse_json"})
    assert response.status_code == status and response.json()["code"] == code
    assert response.json()["detail"] == error.message


def test_erreur_moteur_traduite(make_app):
    class Broken(FakeEngine):
        async def search(self, *a):
            raise EngineError("internal", "PostgreSQL indisponible")

    c = login(TestClient(make_app(FakeProvider([]), engine=Broken())))
    response = c.post("/api/v1/search", json={"query": "parse_json"})
    assert response.status_code == 503 and response.json()["code"] == "engine_internal"


def test_erreur_imprevue_ne_fuit_pas(make_app):
    class Boom(FakeEngine):
        async def search(self, *a):
            raise RuntimeError("mot de passe interne")

    c = login(TestClient(make_app(FakeProvider([]), engine=Boom())))
    response = c.post("/api/v1/search", json={"query": "parse_json"})
    assert response.status_code == 500 and "mot de passe" not in response.text


def test_services_non_initialises():
    app = FastAPI()
    app.include_router(router)
    app.state.ai = None
    assert TestClient(app).post("/api/v1/search", json={"query": "x"}).status_code == 503


def test_deconnexion_du_client_annule_la_recherche(tmp_path):
    class Hang:
        cancelled = False

        async def chat(self, *a, **k):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                Hang.cancelled = True
                raise

    engine = FakeEngine()
    accounts = AccountStore(tmp_path / "accounts.db")
    user = accounts.register("alice", "motdepasse-ok")
    store_ = UserConfigStore(accounts, user["id"], user["dek"])
    store_.save("ollama", __import__("conftest").make_config())
    service = SearchService(None, engine, provider_factory=lambda r, t=None: Hang())
    services = AIServices(None, engine, service, accounts=accounts)

    class Gone:
        calls = 0

        async def is_disconnected(self):
            # Connecté au premier passage (le pipeline démarre), puis parti.
            Gone.calls += 1
            return Gone.calls > 1

    async def scenario():
        return await api_module.search(SearchRequest(query="parse_json"), Gone(), services, user, store_)

    response = run(scenario())
    assert response.status_code == 499 and json.loads(response.body)["code"] == "cancelled"
    assert Hang.cancelled  # la tâche du pipeline a bien été annulée
