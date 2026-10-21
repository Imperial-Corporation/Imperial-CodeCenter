# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Nouveautés 1.0.4 : administration, jetons d'API, accès direct au code, sauvegardes, profils, métriques."""

import json
import os
import tarfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine
from icc_ai import accounts_api, admin_panel_api, api, backup, code_api, profile, sysinfo
from icc_ai.accounts import AccountStore
from icc_ai.chat import ChatService
from icc_ai.service import SearchService
from icc_ai.services import AIServices

PW = "motdepasse-ok"


@pytest.fixture
def env(tmp_path, monkeypatch, store):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CODECENTER_DATA_DIR", str(data))
    monkeypatch.setenv("CODECENTER_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.delenv("CODECENTER_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("CODECENTER_METRICS_TOKEN", raising=False)
    monkeypatch.delenv("CODECENTER_METRICS_PUBLIC", raising=False)
    monkeypatch.setenv("CODECENTER_USER_RATE", "0")
    engine = FakeEngine()
    accounts = AccountStore(data / "accounts.db")
    service = SearchService(store, engine)
    app = FastAPI()
    for router in (api.router, accounts_api.router, admin_panel_api.router, admin_panel_api.public_router, code_api.router):
        app.include_router(router)
    app.state.ai = AIServices(store, engine, service, ChatService(service), accounts=accounts)
    return app, data


def signup(client, name):
    r = client.post("/api/v1/auth/register", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture
def admin(env):
    client = TestClient(env[0])
    signup(client, "boss")
    return client


# ---- administration -------------------------------------------------------

def test_admin_reserve_a_ladministrateur(env):
    app, _ = env
    boss, other = TestClient(app), TestClient(app)
    signup(boss, "boss")
    signup(other, "alice")
    assert boss.get("/api/v1/admin/users").status_code == 200
    assert other.get("/api/v1/admin/users").status_code == 403
    assert TestClient(app).get("/api/v1/admin/users").status_code == 401


def test_desactiver_reinitialiser_supprimer(env):
    app, _ = env
    boss, alice = TestClient(app), TestClient(app)
    signup(boss, "boss")
    signup(alice, "alice")
    users = {u["username"]: u for u in boss.get("/api/v1/admin/users").json()["users"]}
    uid = users["alice"]["id"]
    assert users["boss"]["is_admin"] and not users["alice"]["disabled"]

    assert boss.post(f"/api/v1/admin/users/{uid}/disabled", json={"disabled": True}).status_code == 200
    assert alice.get("/api/v1/auth/me").json()["user"] is None  # déconnectée aussitôt
    r = TestClient(app).post("/api/v1/auth/login", json={"username": "alice", "password": PW})
    assert r.status_code == 403 and r.json()["code"] == "account_disabled"
    assert boss.post(f"/api/v1/admin/users/{uid}/disabled", json={"disabled": False}).status_code == 200

    assert boss.post(f"/api/v1/admin/users/{uid}/password", json={"password": "nouveau-mdp-ok"}).status_code == 200
    assert TestClient(app).post("/api/v1/auth/login", json={"username": "alice", "password": PW}).status_code == 401
    assert TestClient(app).post("/api/v1/auth/login", json={"username": "alice", "password": "nouveau-mdp-ok"}).status_code == 200

    me = users["boss"]["id"]
    assert boss.delete(f"/api/v1/admin/users/{me}").status_code == 409  # l'administrateur est protégé
    assert boss.post(f"/api/v1/admin/users/{me}/disabled", json={"disabled": True}).status_code == 409
    assert boss.delete(f"/api/v1/admin/users/{uid}").status_code == 200
    assert [u["username"] for u in boss.get("/api/v1/admin/users").json()["users"]] == ["boss"]


def test_fermer_les_inscriptions_depuis_ladmin(env):
    app, _ = env
    boss = TestClient(app)
    signup(boss, "boss")
    assert boss.put("/api/v1/admin/registration", json={"open": False}).json() == {"registration_open": False}
    assert TestClient(app).get("/api/v1/auth/me").json()["registration_open"] is False
    r = TestClient(app).post("/api/v1/auth/register", json={"username": "eve", "password": PW})
    assert r.status_code == 403
    boss.put("/api/v1/admin/registration", json={"open": True})
    assert TestClient(app).post("/api/v1/auth/register", json={"username": "eve", "password": PW}).status_code == 200


def test_exclusions_ecrites_et_nettoyees(admin, env):
    r = admin.put("/api/v1/admin/index/exclusions", json={"patterns": ["*.min.js", "  node_modules ", "", "x" * 500]})
    assert r.json()["patterns"] == ["*.min.js", "node_modules"]
    assert admin.get("/api/v1/admin/index/exclusions").json()["patterns"] == ["*.min.js", "node_modules"]
    assert (env[1] / "exclude.txt").read_text() == "*.min.js\nnode_modules\n"
    # l'enregistrement déclenche un passage d'indexation (si le moteur le permet)
    app = env[0]
    asked = []
    app.state.ai.engine.rescan = lambda: asked.append(1)
    admin.put("/api/v1/admin/index/exclusions", json={"patterns": ["x"]})
    assert asked == [1]


def test_systeme_et_index(admin):
    system = admin.get("/api/v1/admin/system").json()
    assert {"process", "machine", "disk", "uptime_s"} <= set(system)
    # le faux moteur n'a pas de rescan : réponse claire, pas d'erreur 500
    assert admin.post("/api/v1/admin/index/rescan").status_code == 501


# ---- jetons d'API ------------------------------------------------------------

def test_jeton_dapi_ouvre_la_recherche_mais_pas_le_reste(env):
    app, _ = env
    owner = TestClient(app)
    signup(owner, "boss")
    created = owner.post("/api/v1/auth/tokens", json={"name": "cli", "expires_days": 30}).json()
    token = created["token"]
    assert token.startswith("icc_pat_")
    assert "token" not in owner.get("/api/v1/auth/tokens").json()["tokens"][0]

    bare = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    found = bare.get("/api/v1/code/search", params={"q": "json"}, headers=headers)
    assert found.status_code == 200 and found.json()["results"]
    assert bare.get("/api/v1/code/search", params={"q": "json"}).status_code == 401
    # jamais pour la gestion du compte ni l'administration, même pour l'administrateur
    assert bare.get("/api/v1/auth/tokens", headers=headers).status_code == 401
    assert bare.post("/api/v1/auth/tokens", json={"name": "x"}, headers=headers).status_code == 401
    assert bare.get("/api/v1/admin/users", headers=headers).status_code == 401
    assert bare.post("/api/v1/auth/delete", json={"password": PW}, headers=headers).status_code == 401
    assert bare.get("/api/v1/auth/me", headers=headers).json()["user"] is None

    token_id = created["id"]
    assert owner.delete(f"/api/v1/auth/tokens/{token_id}").status_code == 200
    assert bare.get("/api/v1/code/search", params={"q": "json"}, headers=headers).status_code == 401


def test_jeton_survit_au_changement_de_mot_de_passe_et_pas_a_la_desactivation(env):
    app, _ = env
    boss, alice = TestClient(app), TestClient(app)
    signup(boss, "boss")
    signup(alice, "alice")
    token = alice.post("/api/v1/auth/tokens", json={"name": "t"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    alice.post("/api/v1/auth/password", json={"current": PW, "new": "autre-mdp-ok1"})
    assert TestClient(app).get("/api/v1/code/filters", headers=h).status_code in (200, 503)
    uid = next(u["id"] for u in boss.get("/api/v1/admin/users").json()["users"] if u["username"] == "alice")
    boss.post(f"/api/v1/admin/users/{uid}/disabled", json={"disabled": True})
    assert TestClient(app).get("/api/v1/code/search", params={"q": "json"}, headers=h).status_code == 401


def test_jeton_expire_et_limite(env):
    app, data = env
    store = app.state.ai.accounts
    user = store.register("zoe", PW)
    store._clock = lambda: 1_000_000.0
    created = store.create_token(user["id"], user["dek"], "court", 1)
    store._clock = lambda: 1_000_000.0 + 2 * 86400
    assert store.user_for_token(created["token"]) is None
    store._clock = lambda: 1_000_000.0
    for i in range(19):
        store.create_token(user["id"], user["dek"], f"t{i}")
    with pytest.raises(Exception) as info:
        store.create_token(user["id"], user["dek"], "trop")
    assert getattr(info.value, "code", "") == "too_many_tokens"


# ---- sauvegardes -------------------------------------------------------------

def test_sauvegarde_via_admin_et_restauration(admin, env, tmp_path):
    app, data = env
    (data / "etat.json").write_text("{}")
    r = admin.post("/api/v1/admin/backups", json={"passphrase": "phrase-secrete"})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert name.endswith(".icc-backup")
    assert admin.get("/api/v1/admin/backups").json()["backups"][0]["encrypted"] is True
    body = admin.get(f"/api/v1/admin/backups/{name}").content
    assert b"accounts.db" not in body and b"boss" not in body  # chiffré
    assert admin.get("/api/v1/admin/backups/..%2Fetc%2Fpasswd").status_code == 404
    assert admin.post("/api/v1/admin/backups", json={"passphrase": "court"}).status_code == 422

    archive = data / "backups" / name
    target = tmp_path / "restored"
    with pytest.raises(backup.BackupError):
        backup.restore_backup(archive, target, passphrase="mauvaise")
    report = backup.restore_backup(archive, target, passphrase="phrase-secrete")
    assert report["accounts"] and (target / "etat.json").exists()
    assert AccountStore(target / "accounts.db").list_users()[0]["username"] == "boss"
    assert admin.delete(f"/api/v1/admin/backups/{name}").status_code == 200


def test_restauration_refuse_les_chemins_suspects(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        manifest = json.dumps({"format": 1, "files": {}}).encode()
        import io

        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        bad = tarfile.TarInfo("../../evasion.txt")
        bad.size = 1
        tar.addfile(bad, io.BytesIO(b"x"))
    with pytest.raises(backup.BackupError):
        backup.restore_backup(evil, tmp_path / "out")
    assert not (tmp_path / "evasion.txt").exists()


def test_rotation_des_sauvegardes(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    for i in range(4):
        backup.create_backup(data, tmp_path / "o", clock=lambda i=i: 1_700_000_000 + i * 10)
    assert len(backup.list_backups(tmp_path / "o")) == 4
    backup.prune(tmp_path / "o", 2)
    assert len(backup.list_backups(tmp_path / "o")) == 2


# ---- métriques, profils ------------------------------------------------------

def test_metrics_protegees_puis_ouvertes(env, monkeypatch):
    app, _ = env
    boss = TestClient(app)
    signup(boss, "boss")
    anon = TestClient(app)
    assert anon.get("/metrics").status_code == 401
    text = boss.get("/metrics").text
    assert "codecenter_info" in text and "codecenter_users 1" in text
    monkeypatch.setenv("CODECENTER_METRICS_TOKEN", "sonde-secrete")
    assert anon.get("/metrics", headers={"Authorization": "Bearer sonde-secrete"}).status_code == 200
    assert anon.get("/metrics", headers={"Authorization": "Bearer faux"}).status_code == 401
    monkeypatch.setenv("CODECENTER_METRICS_PUBLIC", "1")
    assert anon.get("/metrics").status_code == 200


def test_profils_ne_remplacent_jamais_un_reglage_explicite(monkeypatch):
    for key in profile.PROFILES["lite"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODECENTER_PROFILE", "lite")
    monkeypatch.setenv("CODECENTER_ENGINE_THREADS", "8")
    monkeypatch.setenv("CODECENTER_NICE", "0")
    result = profile.apply()
    assert result["profile"] == "lite"
    assert os.environ["CODECENTER_ENGINE_THREADS"] == "8"
    assert os.environ["CODECENTER_DB_MAX_CONNECTIONS"] == "4"
    for key in profile.PROFILES["lite"]:
        monkeypatch.delenv(key, raising=False)


def test_profil_par_defaut_inchange(monkeypatch):
    monkeypatch.delenv("CODECENTER_PROFILE", raising=False)
    assert profile.resolve_name() == "standard"
    monkeypatch.setenv("CODECENTER_PROFILE", "n'importe quoi")
    assert profile.resolve_name() == "standard"
    assert profile.PROFILES["standard"] == {}


def test_prometheus_format():
    text = sysinfo.prometheus(
        stats={"documents_total": 3}, scan={"scan": {"running": True, "seen": 5}, "generation": 2},
        system=sysinfo.snapshot(__import__("pathlib").Path("."), None), users=2, version="9.9.9")
    assert 'codecenter_info{version="9.9.9"} 1' in text
    assert "codecenter_documents_total 3" in text and "codecenter_index_scan_running 1" in text
    assert all(line.startswith("#") or " " in line for line in text.splitlines() if line)
