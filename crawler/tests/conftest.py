# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Outils communs : faux GitHub (httpx.MockTransport), horloge factice, générateurs de contenu."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx
import pytest

from icc_crawler.crawler import Budget, Clock, CrawlConfig, Crawler
from icc_crawler.filters import FilterConfig
from icc_crawler.github import GitHubClient
from icc_crawler.storage import Store

TOKEN = "ghp_" + "A1b2C3d4E5" * 3 + "Zz9Yy8"   # faux jeton, 36 caractères après ghp_


def py_code(tag: str, extra: str = "") -> bytes:
    """Fichier Python valide d'environ 400 octets, unique par `tag`."""
    body = f'''"""Module {tag}."""

import math


def calcul_{tag.replace("-", "_")}(valeurs):
    """Calcule une moyenne pondérée pour {tag}."""
    total = 0.0
    poids = 0.0
    for index, valeur in enumerate(valeurs, start=1):
        total += valeur * index
        poids += index
    if poids == 0:
        return 0.0
    return math.floor(total / poids * 100) / 100


def principal():
    print(calcul_{tag.replace("-", "_")}([1, 2, 3, 4, 5]))
{extra}
'''
    return body.encode("utf-8")


API_CODE = py_code("api", "\nfrom flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef accueil():\n    return 'ok'\n")
ALGO_CODE = py_code("algo", "\n# recherche: binary search et quick sort classiques\n")


class FakeClock:
    """Horloge contrôlable : `monotonic()` en secondes, `now()` en datetime aware."""

    def __init__(self, start: datetime | None = None):
        self.t = 0.0
        self.base = start or datetime(2026, 10, 11, 22, 0, 0, tzinfo=timezone.utc)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return self.base + timedelta(seconds=self.t)

    def as_clock(self) -> Clock:
        return Clock(monotonic=self.monotonic, now=self.now)


@dataclass
class FakeRepo:
    full_name: str
    files: dict[str, bytes] = field(default_factory=dict)
    license: str | None = "MIT"
    stars: int = 500
    branch: str = "main"
    commit: str = ""
    topics: list[str] = field(default_factory=list)
    description: str = ""
    fork: bool = False
    archived: bool = False
    license_text: str = "MIT License\n\nCopyright (c) Test\n"

    def __post_init__(self):
        if not self.commit:
            self.commit = hashlib.sha1(self.full_name.encode()).hexdigest()


class FakeGitHub:
    """Faux serveur : search/repositories, repos, branches, git/trees, license et raw."""

    def __init__(self, *repos: FakeRepo):
        self.repos: dict[str, FakeRepo] = {r.full_name: r for r in repos}
        self.log: list[httpx.Request] = []
        self.hook: Callable[[httpx.Request], None] | None = None
        self.override: Callable[[httpx.Request], httpx.Response | None] | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def count(self, kind: str) -> int:
        return sum(1 for r in self.log if self._kind(r) == kind)

    @staticmethod
    def _kind(request: httpx.Request) -> str:
        if request.url.host == "raw.githubusercontent.com":
            return "raw"
        path = request.url.path
        for key in ("/search/", "/branches/", "/git/trees/", "/license"):
            if key in path:
                return key.strip("/")
        return "repo"

    def raw_paths(self) -> list[str]:
        return ["/".join(r.url.path.split("/")[4:]) for r in self.log if self._kind(r) == "raw"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.log.append(request)
        if self.hook:
            self.hook(request)
        if self.override:
            forced = self.override(request)
            if forced is not None:
                return forced
        if request.url.host == "raw.githubusercontent.com":
            owner, name, sha, path = request.url.path.lstrip("/").split("/", 3)
            repo = self.repos.get(f"{owner}/{name}")
            if repo and sha == repo.commit and path in repo.files:
                return httpx.Response(200, content=repo.files[path])
            return httpx.Response(404, text="404: Not Found")

        path = request.url.path
        if path == "/search/repositories":
            page = int(request.url.params.get("page", "1"))
            items = [self._item(r) for r in self.repos.values()] if page == 1 else []
            return httpx.Response(200, json={"total_count": len(items), "items": items})

        parts = path.strip("/").split("/")          # repos/owner/name/...
        if parts[0] != "repos" or len(parts) < 3:
            return httpx.Response(404, json={"message": "Not Found"})
        repo = self.repos.get(f"{parts[1]}/{parts[2]}")
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        rest = parts[3:]
        if not rest:
            return httpx.Response(200, json=self._item(repo))
        if rest[0] == "branches":
            return httpx.Response(200, json={"name": rest[1], "commit": {"sha": repo.commit}})
        if rest[0] == "git" and rest[1] == "trees":
            tree = [{"path": "src", "type": "tree"}]
            tree += [{"path": p, "type": "blob", "size": len(c)} for p, c in repo.files.items()]
            return httpx.Response(200, json={"sha": repo.commit, "tree": tree, "truncated": False})
        if rest[0] == "license":
            if repo.license is None:
                return httpx.Response(404, json={"message": "Not Found"})
            content = base64.encodebytes(repo.license_text.encode()).decode()
            return httpx.Response(200, json={"content": content, "encoding": "base64"})
        return httpx.Response(404, json={"message": "Not Found"})

    @staticmethod
    def _item(repo: FakeRepo) -> dict:
        return {
            "full_name": repo.full_name, "stargazers_count": repo.stars, "default_branch": repo.branch,
            "license": {"spdx_id": repo.license} if repo.license else None,
            "topics": repo.topics, "description": repo.description,
            "fork": repo.fork, "archived": repo.archived,
        }


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make_client(fake: FakeGitHub, sleeps: list[float], token: str | None = TOKEN, **kwargs) -> GitHubClient:
    kwargs.setdefault("clock", lambda: 1_000_000.0)
    return GitHubClient(token, transport=fake.transport(), sleep=sleeps.append, **kwargs)


def make_budget(**kwargs) -> Budget:
    kwargs.setdefault("min_free_bytes", 0)      # les tests ne dépendent pas du disque réel
    return Budget(**kwargs)


def make_config(destination: Path, **kwargs) -> CrawlConfig:
    kwargs.setdefault("languages", ["python"])
    kwargs.setdefault("filters", FilterConfig(languages=frozenset({"python", "javascript"})))
    return CrawlConfig(destination=destination, **kwargs)


@dataclass
class Run:
    crawler: Crawler
    store: Store | None
    client: GitHubClient
    sleeps: list[float]

    @property
    def stats(self):
        return self.crawler.stats


def start(fake: FakeGitHub, destination: Path, *, budget: Budget | None = None, use_store: bool = True,
          token: str | None = TOKEN, client_kwargs: dict | None = None, **config) -> Run:
    sleeps: list[float] = []
    client = make_client(fake, sleeps, token, **(client_kwargs or {}))
    store = Store(destination) if use_store else None
    crawler = Crawler(make_config(destination, **config), client, store,
                      budget=budget or make_budget(), echo=lambda text: None)
    return Run(crawler, store, client, sleeps)


def crawl(fake: FakeGitHub, destination: Path, **kwargs):
    """Lance une exécution complète et ferme les ressources ; retourne les Stats."""
    run = start(fake, destination, **kwargs)
    try:
        return run.crawler.run()
    finally:
        run.client.close()
        if run.store:
            run.store.close()


def files_on_disk(destination: Path) -> list[Path]:
    return sorted(p for p in destination.rglob("*") if p.is_file() and ".crawler" not in p.parts)


def all_text(*chunks: str) -> str:
    return "\n".join(chunks)


def json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _close_stores(monkeypatch):
    """Ferme tous les Store ouverts pendant un test (évite les connexions SQLite orphelines)."""
    opened: list[Store] = []
    original = Store.__init__

    def tracking(self, *args, **kwargs):
        original(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(Store, "__init__", tracking)
    yield
    for store in opened:
        try:
            store.close()
        except Exception:
            pass
