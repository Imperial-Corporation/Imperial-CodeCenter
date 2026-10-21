# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""CLI `icc` et serveur MCP, contre une vraie instance HTTP (uvicorn en thread)."""

import io
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeEngine
from icc_ai import accounts_api, api, cli, code_api
from icc_ai.accounts import AccountStore
from icc_ai.chat import ChatService
from icc_ai.service import SearchService
from icc_ai.services import AIServices


@pytest.fixture
def server(tmp_path, store, monkeypatch):
    monkeypatch.setenv("CODECENTER_USER_RATE", "0")
    engine = FakeEngine()
    service = SearchService(store, engine)
    app = FastAPI()
    for router in (api.router, accounts_api.router, code_api.router):
        app.include_router(router)
    app.state.ai = AIServices(store, engine, service, ChatService(service),
                              accounts=AccountStore(tmp_path / "a.db"))
    owner = TestClient(app)
    owner.post("/api/v1/auth/register", json={"username": "alice", "password": "motdepasse-ok"})
    token = owner.post("/api/v1/auth/tokens", json={"name": "cli"}).json()["token"]

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield cli.Client(f"http://127.0.0.1:{port}", token), cli.Client(f"http://127.0.0.1:{port}", "")
    srv.should_exit = True
    thread.join(5)


def test_client_recherche_lecture_filtres(server):
    client, _ = server
    data = client.search("json", 5)
    assert data["results"] and "json_tools.py" in cli.format_results(data)
    doc_id = data["results"][0]["document_id"]
    assert "def" in client.document(doc_id, 1, 5)["content"]
    assert client.filters()["languages"][0]["slug"] == "python"


def test_client_sans_jeton_message_clair(server):
    _, anonymous = server
    with pytest.raises(cli.ClientError, match="jeton"):
        anonymous.search("json")


def test_client_instance_injoignable():
    with pytest.raises(cli.ClientError, match="injoignable"):
        cli.Client("http://127.0.0.1:9", "x").search("a")


def test_mcp_protocole(server):
    client, _ = server
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_code", "arguments": {"query": "json"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_code", "arguments": {"document_id": "pas-un-uuid"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        {"jsonrpc": "2.0", "id": 6, "method": "ping"},
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nnot json\n")
    stdout = io.StringIO()
    cli.serve_mcp(client, stdin, stdout)
    replies = [json.loads(l) for l in stdout.getvalue().splitlines()]
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert {t["name"] for t in by_id[2]["result"]["tools"]} == {"search_code", "read_code", "list_filters"}
    assert "json_tools.py" in by_id[3]["result"]["content"][0]["text"] and not by_id[3]["result"]["isError"]
    assert by_id[4]["result"]["isError"] is True
    assert by_id[5]["error"]["code"] == -32601 and by_id[6]["result"] == {}
    assert None in by_id and by_id[None]["error"]["code"] == -32700  # JSON invalide
    assert len(replies) == 7  # la notification n'a pas de réponse


def test_config_fichier_prive(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("ICC_URL", raising=False)
    monkeypatch.delenv("ICC_TOKEN", raising=False)
    path = cli.save_config("http://x.test/", "icc_pat_abc")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert cli.load_config() == {"url": "http://x.test", "token": "icc_pat_abc"}
    monkeypatch.setenv("ICC_TOKEN", "autre")
    assert cli.load_config()["token"] == "autre"
