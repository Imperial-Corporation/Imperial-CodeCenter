# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Tests de bout en bout de l'interface (Playwright + Chromium).

L'interface réelle (dossier `frontend/`) est servie par un petit serveur statique ; l'API est
simulée par des routes Playwright, ce qui rend les tests rapides et déterministes (aucun moteur,
aucune base). L'API réelle est couverte par les tests d'intégration.

    pip install playwright pytest && playwright install chromium
    pytest e2e -q
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api", reason="Playwright non installé")

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0]
        if path in ("/", ""):
            return str(FRONTEND / "index.html")
        if path.startswith("/static/"):
            return str(FRONTEND / path[len("/static/"):])
        return str(FRONTEND / "__missing__")

    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def base_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        yield browser
        browser.close()


class FakeApi:
    """API simulée : état modifiable par les tests, requêtes journalisées."""

    def __init__(self, admin=True, user="alice"):
        self.user = user
        self.admin = admin
        self.registration_open = True
        self.calls: list[tuple[str, str]] = []
        self.tokens: list[dict] = []
        self.users = [
            {"id": "u1", "username": "alice", "created_at": 1_700_000_000, "disabled": False, "last_login": 1_700_100_000,
             "chats": 3, "sessions": 1, "tokens": 0, "is_admin": True},
            {"id": "u2", "username": "bob", "created_at": 1_700_200_000, "disabled": False, "last_login": None,
             "chats": 0, "sessions": 0, "tokens": 1, "is_admin": False},
        ]
        self.scan = {"running": False, "seen": 120, "indexed": 4, "unchanged": 116, "failed": 0, "excluded": 2,
                     "finished_unix": 1_700_300_000, "last_duration_secs": 12, "pause_ms": 20, "previous_total": 120,
                     "current": "", "kind": "periodic"}
        self.exclusions: list[str] = ["*.min.js"]
        self.backups: list[dict] = []

    def handle(self, route):
        request = route.request
        path = request.url.split("/api/v1", 1)[-1].split("?", 1)[0]
        method = request.method
        self.calls.append((method, path))
        body = {}
        if request.post_data:
            try:
                body = json.loads(request.post_data)
            except ValueError:
                body = {}

        def ok(data, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

        if path == "/auth/me":
            data = {"enabled": True, "registration_open": self.registration_open,
                    "user": {"username": self.user} if self.user else None}
            if self.user:
                data["is_admin"] = self.admin
            return ok(data)
        if path == "/about":
            return ok({"name": "Imperial CodeCenter", "organization": "Imperial", "version": "1.0.4",
                       "license": "Imperial Community License 1.0", "website": "https://imperial-corporation.pages.dev/icc"})
        if path == "/chats":
            return ok({"chats": []})
        if path == "/ai/catalog":
            return ok({"providers": [], "defaults": {}})
        if path == "/ai/config":
            return ok({"active": None, "providers": {}})
        if path == "/auth/tokens" and method == "GET":
            return ok({"tokens": self.tokens})
        if path == "/auth/tokens" and method == "POST":
            token = {"id": "t1", "name": body.get("name", "x"), "created_at": 1_700_400_000, "expires_at": None, "last_used": None}
            self.tokens.append(token)
            return ok({**token, "token": "icc_pat_t1_SECRETVALUE"})
        if path.startswith("/auth/tokens/") and method == "DELETE":
            self.tokens = [t for t in self.tokens if t["id"] != path.rsplit("/", 1)[-1]]
            return ok({"ok": True})
        if path == "/admin/users":
            return ok({"users": self.users, "registration_open": self.registration_open})
        if path == "/admin/registration":
            self.registration_open = bool(body.get("open"))
            return ok({"registration_open": self.registration_open})
        if path.endswith("/disabled"):
            uid = path.split("/")[-2]
            for u in self.users:
                if u["id"] == uid:
                    u["disabled"] = bool(body.get("disabled"))
            return ok({"ok": True})
        if path == "/admin/index":
            return ok({"scan": self.scan, "generation": 3, "stats": {"documents_total": 120}})
        if path == "/admin/index/rescan":
            self.scan = {**self.scan, "running": True, "kind": "manual", "seen": 10, "indexed": 5, "previous_total": 120}
            return ok({"ok": True})
        if path == "/admin/index/exclusions" and method == "GET":
            return ok({"patterns": self.exclusions, "env": "", "file": "/data/exclude.txt"})
        if path == "/admin/index/exclusions" and method == "PUT":
            self.exclusions = [p for p in body.get("patterns", []) if p]
            return ok({"patterns": self.exclusions})
        if path == "/admin/system":
            return ok({"uptime_s": 4000, "process": {"rss": 120 * 1024 ** 2, "rss_peak": 150 * 1024 ** 2, "threads": 12, "pid": 1},
                       "machine": {"cpus": 4, "load": [0.2, 0.3, 0.4], "mem_total": 8 * 1024 ** 3, "mem_available": 5 * 1024 ** 3},
                       "disk": {"data": {"total": 100 * 1024 ** 3, "free": 60 * 1024 ** 3, "used": 40 * 1024 ** 3}, "codes": None,
                                "accounts_db": 40960},
                       "profile": {"profile": "lite"}})
        if path == "/admin/backups" and method == "GET":
            return ok({"backups": self.backups, "keep": "7", "every_hours": "0"})
        if path == "/admin/backups" and method == "POST":
            self.backups.insert(0, {"name": "icc-backup-20261020-120000.tar.gz", "size": 2048, "created": 1_700_500_000, "encrypted": False})
            return ok({"name": self.backups[0]["name"], "size": 2048})
        if path == "/crawler":
            return ok({"job": None, "history": [], "token": "aucun", "disk": {"free_gb": 50, "total_gb": 100}})
        if path == "/index/health":
            return ok({"failures": [], "read_only_directories": [], "skipped": [], "indexed_in_session": 0, "disk": {}})
        route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "inconnu"}))


@pytest.fixture
def make_page(browser, base_url):
    pages = []

    def make(api: FakeApi | None = None, locale="fr-FR"):
        context = browser.new_context(locale=locale, viewport={"width": 1100, "height": 800})
        pages.append(context)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
        api = api or FakeApi()
        page.route("**/api/v1/**", api.handle)
        page.route("**/ws/status", lambda r: r.abort())
        page.route("**/api/health", lambda r: r.fulfill(status=200, content_type="application/json", body='{"status":"ok"}'))
        page.route("**/api/v1/stats", lambda r: r.fulfill(status=200, content_type="application/json", body='{"documents_total":1}'))
        page.goto(base_url + "/")
        page.wait_for_load_state("domcontentloaded")
        page.errors = errors
        return page, api

    yield make
    for context in pages:
        context.close()
