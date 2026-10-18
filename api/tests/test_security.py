# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Garde-fou SSRF, limites de débit, en-têtes de l'API."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine, FakeProvider, turn
from icc_ai import accounts_api, api
from icc_ai.accounts import AccountError, AccountStore
from icc_ai.errors import ConfigError
from icc_ai.netguard import check_provider_url
from icc_ai.proxy import ProxyMiddleware, ProxySettings
from icc_ai.service import SearchService
from icc_ai.services import AIServices


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data", "http://[fe80::1]/v1", "http://0.0.0.0:8000",
    "http://224.0.0.1/", "http://[::]/",
])
def test_adresses_dangereuses_toujours_refusees(url, monkeypatch):
    monkeypatch.setenv("CODECENTER_PROVIDER_NETWORK", "any")
    with pytest.raises(ConfigError):
        check_provider_url(url)


def test_local_permis_en_usage_local_et_refuse_en_instance_publique(monkeypatch):
    monkeypatch.setenv("CODECENTER_PROVIDER_NETWORK", "any")
    for url in ("http://127.0.0.1:11434", "http://192.168.1.20:1234/v1", "http://localhost:8080"):
        check_provider_url(url)
    monkeypatch.setenv("CODECENTER_PROVIDER_NETWORK", "public")
    for url in ("http://127.0.0.1:11434", "http://10.0.0.5/v1", "http://[::1]:11434", "http://localhost:8080"):
        with pytest.raises(ConfigError):
            check_provider_url(url)
    check_provider_url("https://93.184.216.34/v1")  # adresse publique


def test_mode_public_par_defaut_derriere_un_proxy(monkeypatch):
    monkeypatch.delenv("CODECENTER_PROVIDER_NETWORK", raising=False)
    monkeypatch.setenv("CODECENTER_PROXY_MODE", "cloudflare")
    with pytest.raises(ConfigError):
        check_provider_url("http://127.0.0.1:11434")
    monkeypatch.setenv("CODECENTER_PROXY_MODE", "off")
    check_provider_url("http://127.0.0.1:11434")


def build(tmp_path):
    engine = FakeEngine()
    service = SearchService(None, engine, provider_factory=lambda r, t=None: FakeProvider([turn("{}")] * 50))
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(accounts_api.router)
    app.state.ai = AIServices(None, engine, service, accounts=AccountStore(tmp_path / "a.db"))
    return app


def signup(app, name="alice"):
    c = TestClient(app)
    assert c.post("/api/v1/auth/register", json={"username": name, "password": "motdepasse-ok"}).status_code == 200
    return c


def test_url_interdite_refusee_a_l_enregistrement_et_au_test(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECENTER_PROVIDER_NETWORK", "public")
    c = signup(build(tmp_path))
    body = {"base_url": "http://169.254.169.254/latest", "model": "m", "activate": True}
    saved = c.put("/api/v1/ai/providers/ollama", json=body)
    assert saved.status_code == 422
    tested = c.post("/api/v1/ai/providers/ollama/test", json=body).json()
    assert tested["ok"] is False and tested["kind"] == "config"
    assert c.get("/api/v1/ai/config").json()["active"] is None


def test_limite_de_requetes_ia_par_compte(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECENTER_USER_RATE", "3")
    monkeypatch.setattr(api, "_user_rate", api._UserRate())
    app = build(tmp_path)
    alice, bob = signup(app, "alice"), signup(app, "bobby")
    codes = [alice.post("/api/v1/search", json={"query": "parse_json"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    assert int(alice.post("/api/v1/search", json={"query": "x"}).headers["retry-after"]) > 0
    assert bob.post("/api/v1/search", json={"query": "parse_json"}).status_code == 200  # autre compte non touché


def test_creations_de_compte_limitees_par_ip(tmp_path):
    store = AccountStore(tmp_path / "r.db")
    for i in range(10):
        store.register(f"user{i:02d}", "motdepasse-ok", ip="1.2.3.4")
    with pytest.raises(AccountError) as err:
        store.register("user99", "motdepasse-ok", ip="1.2.3.4")
    assert err.value.status == 429 and err.value.retry_after
    store.register("user98", "motdepasse-ok", ip="5.6.7.8")  # autre adresse : permis


def test_api_jamais_en_cache(tmp_path):
    app = build(tmp_path)
    app.add_middleware(ProxyMiddleware, settings=ProxySettings("off"))
    r = TestClient(app).get("/api/v1/auth/me")
    assert r.headers["cache-control"] == "no-store"
