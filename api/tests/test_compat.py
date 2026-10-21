# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Rétrocompatibilité 1.0.3 → 1.0.4 : rien de ce que la 1.0.3 exposait ou stockait ne doit casser.

Voir docs/compatibilite.md. Ces tests figent l'API publique de la 1.0.3 : si l'un d'eux échoue,
c'est un changement incompatible, à décider consciemment (et à noter au CHANGELOG).
"""

import os
import sqlite3
import sys
import types

import pytest

# Routes de la 1.0.3 (méthode, chemin) : elles doivent toutes exister encore.
ROUTES_1_0_3 = [
    ("DELETE", "/api/v1/chats/{chat_id}"), ("GET", "/api/health"), ("GET", "/api/v1/about"),
    ("GET", "/api/v1/ai/catalog"), ("GET", "/api/v1/ai/config"), ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/chats"), ("GET", "/api/v1/crawler"), ("GET", "/api/v1/index/health"),
    ("GET", "/api/v1/library/info"), ("GET", "/api/v1/proxy/check"), ("GET", "/api/v1/stats"),
    ("GET", "/api/v1/status"), ("PATCH", "/api/v1/chats/{chat_id}"),
    ("POST", "/api/v1/ai/providers/{provider_id}/test"), ("POST", "/api/v1/auth/delete"),
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/logout"), ("POST", "/api/v1/auth/password"),
    ("POST", "/api/v1/auth/register"), ("POST", "/api/v1/chat"), ("POST", "/api/v1/chats"),
    ("POST", "/api/v1/chats/{chat_id}/open"), ("POST", "/api/v1/crawler/start"),
    ("POST", "/api/v1/crawler/stop"), ("POST", "/api/v1/library/depot/scan"),
    ("POST", "/api/v1/library/import"), ("POST", "/api/v1/search"),
    ("PUT", "/api/v1/ai/providers/{provider_id}"), ("PUT", "/api/v1/crawler/token"),
]


@pytest.fixture(scope="module")
def spec():
    if "codecenter_engine" not in sys.modules:
        try:
            import codecenter_engine  # noqa: F401
        except ImportError:
            stub = types.ModuleType("codecenter_engine")
            for name in ("get_stats", "healt", "start_watcher", "stop_watcher"):
                setattr(stub, name, lambda *a, **k: None)
            sys.modules["codecenter_engine"] = stub
    import main

    return main.app.openapi()["paths"]


def test_toutes_les_routes_1_0_3_existent_encore(spec):
    present = {(m.upper(), p) for p, ops in spec.items() for m in ops}
    missing = [r for r in ROUTES_1_0_3 if r not in present]
    assert not missing, f"Routes de la 1.0.3 disparues : {missing}"


def test_base_des_comptes_1_0_3_se_migre_sans_perte(tmp_path):
    """Un accounts.db créé par la 1.0.3 (sans colonnes disabled/last_login, sans settings ni api_tokens)."""
    from icc_ai.accounts import AccountStore

    first = AccountStore(tmp_path / "accounts.db")
    user = first.register("vieux", "motdepasse-ok")
    first.save_ai_config(user["id"], user["dek"], {"active": "ollama"})
    first.close()

    db = sqlite3.connect(tmp_path / "accounts.db")  # on « rétrograde » le schéma comme en 1.0.3
    db.executescript("""
        DROP TABLE settings; DROP TABLE api_tokens;
        CREATE TABLE users_old AS SELECT id, username, password_hash, created_at, kek_salt, wrapped_dek FROM users;
        PRAGMA foreign_keys = OFF;
        DROP TABLE users;
        CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, created_at REAL NOT NULL, kek_salt TEXT, wrapped_dek TEXT);
        INSERT INTO users SELECT * FROM users_old; DROP TABLE users_old;
    """)
    db.commit()
    db.close()

    store = AccountStore(tmp_path / "accounts.db")  # ouverture par la 1.0.4
    again = store.authenticate("vieux", "motdepasse-ok")
    assert again["id"] == user["id"] and again["dek"] == user["dek"]
    assert store.load_ai_config(user["id"], again["dek"]) == {"active": "ollama"}
    assert store.registration_open is True
    assert store.list_users()[0]["disabled"] is False and store.is_admin(user["id"])
    # idempotent : une seconde ouverture ne change rien
    AccountStore(tmp_path / "accounts.db").close()


def test_variable_registration_reste_le_comportement_par_defaut(tmp_path, monkeypatch):
    from icc_ai.accounts import AccountStore

    monkeypatch.setenv("CODECENTER_REGISTRATION", "closed")
    store = AccountStore.from_environment(tmp_path)
    assert store.registration_open is False
    store.registration_open = True  # le réglage de l'administration prime ensuite
    assert AccountStore.from_environment(tmp_path).registration_open is True


def test_sans_profil_aucun_reglage_n_est_impose(monkeypatch):
    from icc_ai import profile

    for key in list(os.environ):
        if key.startswith("CODECENTER_") and key in sum((list(p) for p in profile.PROFILES.values()), []):
            monkeypatch.delenv(key)
    monkeypatch.delenv("CODECENTER_PROFILE", raising=False)
    before = dict(os.environ)
    assert profile.apply() == {"profile": "standard", "applied": {}}
    assert dict(os.environ) == before


def test_reponses_1_0_3_inchangees_pour_un_visiteur(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from conftest import FakeEngine
    from icc_ai import accounts_api, api
    from icc_ai.accounts import AccountStore
    from icc_ai.chat import ChatService
    from icc_ai.service import SearchService
    from icc_ai.services import AIServices

    engine = FakeEngine()
    service = SearchService(None, engine)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(accounts_api.router)
    app.state.ai = AIServices(None, engine, service, ChatService(service), accounts=AccountStore(tmp_path / "a.db"))
    client = TestClient(app)
    assert client.get("/api/v1/auth/me").json() == {"enabled": True, "registration_open": True, "user": None}
    assert client.post("/api/v1/search", json={"query": "json"}).status_code == 401
    created = client.post("/api/v1/auth/register", json={"username": "alice", "password": "motdepasse-ok"})
    assert created.json() == {"user": {"username": "alice"}}
    me = client.get("/api/v1/auth/me").json()
    assert me["user"] == {"username": "alice"}  # clés historiques intactes ; `is_admin` s'y ajoute


def test_migration_004_est_additive():
    """La migration ne supprime ni ne renomme rien : elle ne fait qu'ajouter."""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "config/database/migrations/004_content_index.sql").read_text().lower()
    assert "drop table" not in sql and "drop column" not in sql and "rename" not in sql
    assert "if not exists" in sql
