# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Routes des comptes et des discussions enregistrées (cookie de session, CSRF, restauration)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine, FakeProvider, make_config, turn
from icc_ai import accounts_api, api
from icc_ai.accounts import AccountStore
from icc_ai.chat import ChatService
from icc_ai.service import SearchService
from icc_ai.services import AIServices

ANSWER = json.dumps({"summary": "Réponse **ok**", "used_sources": ["S1"], "follow_ups": ["Et ensuite ?"]})


def build(store, tmp_path, script=None, registration_open=True):
    store.save("ollama", make_config())
    engine = FakeEngine()
    provider = FakeProvider(script or [turn(ANSWER)] * 6)
    service = SearchService(store, engine, provider_factory=lambda r, t=None: provider)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(accounts_api.router)
    accounts = AccountStore(tmp_path / "accounts.db", registration_open=registration_open)
    app.state.ai = AIServices(store, engine, service, ChatService(service), accounts=accounts)
    app.state.provider = provider
    return app


@pytest.fixture
def app(store, tmp_path):
    return build(store, tmp_path)


@pytest.fixture
def client(app):
    return TestClient(app)


def signup(client, name="alice", password="motdepasse-ok"):
    return client.post("/api/v1/auth/register", json={"username": name, "password": password})


def test_me_visiteur_puis_connecte(client):
    data = client.get("/api/v1/auth/me").json()
    assert data == {"enabled": True, "registration_open": True, "user": None}
    assert signup(client).status_code == 200
    assert client.get("/api/v1/auth/me").json()["user"] == {"username": "alice"}


def test_cookie_de_session_securise(client):
    response = signup(client)
    cookie = response.headers["set-cookie"].lower()
    assert "icc_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/" in cookie and "max-age=" in cookie and "secure" not in cookie
    assert "motdepasse" not in response.text


def test_cookie_secure_en_https(app):
    secure = TestClient(app, base_url="https://testserver")
    assert "secure" in signup(secure).headers["set-cookie"].lower()


def test_cookie_secure_force(app, monkeypatch):
    monkeypatch.setenv("CODECENTER_FORCE_HTTPS", "1")
    assert "secure" in signup(TestClient(app)).headers["set-cookie"].lower()


def test_deconnexion_invalide_la_session(client):
    signup(client)
    token = client.cookies.get("icc_session")
    assert client.post("/api/v1/auth/logout").status_code == 200
    client.cookies.set("icc_session", token)  # ancien cookie rejoué : refusé côté serveur
    assert client.get("/api/v1/auth/me").json()["user"] is None


def test_connexion_erreurs(client):
    signup(client)
    client.cookies.clear()
    bad = client.post("/api/v1/auth/login", json={"username": "alice", "password": "faux-faux-faux"})
    assert bad.status_code == 401 and bad.json()["code"] == "bad_credentials"
    assert "icc_session" not in bad.headers.get("set-cookie", "")
    ok = client.post("/api/v1/auth/login", json={"username": "alice", "password": "motdepasse-ok"})
    assert ok.status_code == 200


def test_blocage_apres_trop_d_echecs(client):
    signup(client)
    client.cookies.clear()
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "alice", "password": "faux-faux-faux"})
    blocked = client.post("/api/v1/auth/login", json={"username": "alice", "password": "motdepasse-ok"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_inscription_invalide_ou_doublon_ou_fermee(client, store, tmp_path):
    assert signup(client, "a").status_code == 422
    assert signup(client, "alice", "court").json()["code"] == "weak_password"
    signup(client)
    assert signup(TestClient(client.app), "ALICE").status_code == 409
    closed = TestClient(build(store, tmp_path / "x", registration_open=False))
    assert closed.get("/api/v1/auth/me").json()["registration_open"] is False
    assert signup(closed).status_code == 403


def test_origine_etrangere_refusee(client):
    response = client.post("/api/v1/auth/register", json={"username": "mallory", "password": "motdepasse-ok"},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/v1/auth/me").json()["user"] is None


def test_comptes_desactives(store, tmp_path):
    app = build(store, tmp_path)
    app.state.ai.accounts = None
    c = TestClient(app)
    assert c.get("/api/v1/auth/me").json() == {"enabled": False, "registration_open": False, "user": None}
    assert signup(c).status_code == 503


def test_chats_exigent_la_connexion(client):
    for method, path in [("get", "/api/v1/chats"), ("post", "/api/v1/chats"), ("delete", "/api/v1/chats/x"),
                         ("post", "/api/v1/chats/x/open"), ("post", "/api/v1/auth/password"),
                         ("post", "/api/v1/auth/delete")]:
        kwargs = {"json": {"conversation_id": "x", "current": "a", "new": "b", "password": "c"}} if method == "post" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


def ask(client, query="parse_json"):
    data = client.post("/api/v1/search", json={"query": query}).json()
    assert data["status"] == "ok"
    return data["meta"]["conversation_id"], data


def test_enregistrer_puis_poursuivre_puis_rouvrir(client, app):
    signup(client)
    cid, first = ask(client)
    saved = client.post("/api/v1/chats", json={"conversation_id": cid})
    assert saved.status_code == 200 and saved.json()["title"] == "parse_json"
    chat_id = saved.json()["id"]
    assert client.post("/api/v1/chats", json={"conversation_id": cid}).status_code == 409  # déjà enregistrée

    follow = client.post("/api/v1/chat", json={"conversation_id": cid, "message": "et en async ?"})
    assert follow.status_code == 200 and not follow.json()["warnings"]
    listing = client.get("/api/v1/chats").json()["chats"]
    assert [(c["id"], c["turn_count"]) for c in listing] == [(chat_id, 2)]

    # « Redémarrage » : la mémoire du chat est perdue, la discussion enregistrée se rouvre.
    app.state.ai.chat = ChatService(app.state.ai.search)
    opened = client.post(f"/api/v1/chats/{chat_id}/open").json()
    assert [t["question"] for t in opened["turns"]] == ["parse_json", "et en async ?"]
    assert opened["turns"][0]["response"]["answer"]["summary"] == "Réponse **ok**"
    new_cid = opened["conversation_id"]
    assert all(t["response"]["meta"]["conversation_id"] == new_cid for t in opened["turns"])

    third = client.post("/api/v1/chat", json={"conversation_id": new_cid, "message": "merci, une dernière"})
    assert third.status_code == 200
    assert client.get("/api/v1/chats").json()["chats"][0]["turn_count"] == 3
    # Le registre est reconstruit : le modèle a reçu l'historique et les sources restent S1, S2…
    sent = app.state.provider.calls[-1]["messages"]
    assert any(m.role == "assistant" and "Réponse" in m.content for m in sent)
    assert [s["id"] for s in third.json()["sources"]][:1] == [first["sources"][0]["id"]]


def test_discussion_non_enregistree_n_est_pas_ecrite(client):
    signup(client)
    cid, _ = ask(client)
    client.post("/api/v1/chat", json={"conversation_id": cid, "message": "suite"})
    assert client.get("/api/v1/chats").json()["chats"] == []


def test_autre_utilisateur_ne_voit_ni_n_ecrit(client, app):
    signup(client)
    cid, _ = ask(client)
    chat_id = client.post("/api/v1/chats", json={"conversation_id": cid}).json()["id"]
    bob = TestClient(app)
    signup(bob, "bob")
    assert bob.get("/api/v1/chats").json()["chats"] == []
    assert bob.post(f"/api/v1/chats/{chat_id}/open").status_code == 404
    assert bob.delete(f"/api/v1/chats/{chat_id}").status_code == 404
    assert bob.patch(f"/api/v1/chats/{chat_id}", json={"title": "pirate"}).status_code == 404
    # Bob utilise la conversation d'Alice (id connu) : rien n'est ajouté à sa discussion enregistrée.
    bob.post("/api/v1/chat", json={"conversation_id": cid, "message": "intrus"})
    assert len(client.post(f"/api/v1/chats/{chat_id}/open").json()["turns"]) == 1


def test_enregistrer_conversation_inconnue(client):
    signup(client)
    assert client.post("/api/v1/chats", json={"conversation_id": "inconnue"}).status_code == 404


def test_renommer_et_supprimer(client):
    signup(client)
    cid, _ = ask(client)
    chat_id = client.post("/api/v1/chats", json={"conversation_id": cid, "title": "Mon titre"}).json()["id"]
    assert client.patch(f"/api/v1/chats/{chat_id}", json={"title": "Autre"}).json()["title"] == "Autre"
    assert client.delete(f"/api/v1/chats/{chat_id}").status_code == 200
    assert client.get("/api/v1/chats").json()["chats"] == []


def test_changement_de_mot_de_passe_et_suppression(client, app):
    signup(client)
    assert client.post("/api/v1/auth/password", json={"current": "faux-faux-faux", "new": "nouveau-motdepasse"}).status_code == 401
    assert client.post("/api/v1/auth/password", json={"current": "motdepasse-ok", "new": "nouveau-motdepasse"}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["user"] is not None  # la session courante est conservée
    assert client.post("/api/v1/auth/delete", json={"password": "faux-faux-faux"}).status_code == 401
    assert client.post("/api/v1/auth/delete", json={"password": "nouveau-motdepasse"}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["user"] is None
    assert app.state.ai.accounts.user_count() == 0


def test_restauration_robuste_aux_donnees_anciennes():
    from icc_ai.chat import ChatService
    chat = ChatService.__new__(ChatService)
    ChatService.__init__(chat, search=None)
    cid = chat.restore([{"question": "q", "response": {"answer": {"summary": 5}, "sources": []}},
                        {"question": "q2", "response": {}}])
    session = chat.get(cid)
    assert session.history == [] and session.turns == 2 and len(session.registry) == 0
