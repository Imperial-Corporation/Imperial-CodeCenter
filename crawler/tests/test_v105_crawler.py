# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Version 1.0.5 : paramètres effectifs, mode UNSAFE, suivi persistant, arbres tronqués, migration."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

import httpx
import pytest

from conftest import (
    FakeGitHub, FakeRepo, crawl, files_on_disk, git_blob_sha, json_lines, make_budget, py_code, start,
)
from icc_crawler import cli
from icc_crawler.classify import (
    DEFAULT_CATEGORY, TAXONOMY, Classification, classify_detailed, is_valid_folder_name, slugify_part,
)
from icc_crawler.filters import FilterConfig
from icc_crawler.storage import SCHEMA_VERSION, Store


# -- faux GitHub étendu : recherche paginée par fenêtres d'étoiles, arbres non récursifs ----------------


class PagedGitHub(FakeGitHub):
    """Recherche triée par étoiles (100 par page, 1000 au plus) et arbres tronqués/non récursifs."""

    def __init__(self, *repos, truncate_over: int | None = None):
        super().__init__(*repos)
        self.truncate_over = truncate_over
        self.search_queries: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/repositories":
            self.log.append(request)
            query = request.url.params.get("q", "")
            self.search_queries.append(query)
            low, high = 0, 10 ** 9
            if (m := re.search(r"stars:>=(\d+)", query)):
                low = int(m.group(1))
            if (m := re.search(r"stars:(\d+)\.\.(\d+)", query)):
                low, high = int(m.group(1)), int(m.group(2))
            matching = sorted((r for r in self.repos.values() if low <= r.stars <= high),
                              key=lambda r: (-r.stars, r.full_name))[:1000]
            page = int(request.url.params.get("page", "1"))
            size = int(request.url.params.get("per_page", "100"))
            chunk = matching[(page - 1) * size: page * size]
            return httpx.Response(200, json={"total_count": len(matching), "items": [self._item(r) for r in chunk]})

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/git/trees/([0-9a-fA-F]+|dir:.+)", path)
        if match and self.truncate_over is not None:
            self.log.append(request)
            repo = self.repos[match.group(1)]
            return self._tree(repo, match.group(2), recursive="recursive" in request.url.params)
        return super().__call__(request)

    def _tree(self, repo: FakeRepo, ref: str, recursive: bool) -> httpx.Response:
        if recursive:
            entries = [{"path": p, "type": "blob", "size": len(c), "sha": git_blob_sha(c)}
                       for p, c in list(repo.files.items())[: self.truncate_over]]
            return httpx.Response(200, json={"tree": entries, "truncated": True})
        prefix = "" if ref == repo.commit else ref.removeprefix("dir:")
        level, seen = [], set()
        for p, c in repo.files.items():
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            if "/" in rest:
                folder = rest.split("/", 1)[0]
                if folder not in seen:
                    seen.add(folder)
                    level.append({"path": folder, "type": "tree", "sha": f"dir:{prefix}{folder}/"})
            else:
                level.append({"path": rest, "type": "blob", "size": len(c), "sha": git_blob_sha(c)})
        return httpx.Response(200, json={"tree": level, "truncated": False})


def many_repos(count: int, *, files: int = 1, first_stars: int = 5000) -> list[FakeRepo]:
    return [
        FakeRepo(f"o/r{i:04d}", stars=first_stars - i,
                 files={f"src/m{j}.py": py_code(f"r{i}f{j}") for j in range(files)})
        for i in range(count)
    ]


# -- classification : sous-catégories et « non classé » ----------------------------------------------------


def test_default_category_is_non_classe_and_valid():
    found = classify_detailed("src/foo.py", content="x = 1\n")
    assert found == Classification("non-classe", "", (), 0) and DEFAULT_CATEGORY == "non-classe"


@pytest.mark.parametrize("path,content,category,sub", [
    ("src/api/graphql/schema.py", "", "api", "graphql"),
    ("lib/sorting/quicksort.py", "def quick_sort(x): ...", "algorithmes", "tri"),
    ("tools/cli/main.py", "import argparse\n", "systeme", "ligne-de-commande"),
    ("crypto/aes.py", "from cryptography.fernet import Fernet\nimport hashlib\n", "securite", "chiffrement"),
    ("net/tcp_server.py", "import socket\ns = socket.socket()\n", "reseau", "sockets"),
])
def test_subcategories_are_found(path, content, category, sub):
    found = classify_detailed(path, content=content)
    assert (found.category, found.subcategory) == (category, sub)
    assert found.folder_parts == (category, sub)
    assert found.tags[0] == category and sub in found.tags


def test_tags_include_topics_and_stay_bounded():
    topics = [f"topic-{i}" for i in range(30)] + ["fastapi", "Machine Learning", "../evil"]
    found = classify_detailed("src/api/rest/users.py", topics=topics, content="from fastapi import FastAPI\n")
    assert 1 <= len(found.tags) <= 10
    assert all(is_valid_folder_name(tag) for tag in found.tags)


def test_every_taxonomy_name_is_a_valid_folder_name_and_not_a_language_slug():
    from icc_crawler.filters import LANGUAGES
    languages = {slug for slug, _ in LANGUAGES.values()}
    for category, (_k, _p, subs) in TAXONOMY.items():
        assert is_valid_folder_name(category) and category not in languages
        for sub in subs:
            assert is_valid_folder_name(sub) and sub not in languages


def test_slugify_part_rejects_traversal_and_hidden_names():
    assert slugify_part("Machine Learning") == "machine-learning"
    assert slugify_part("Sécurité") == "securite"
    assert slugify_part("../..") is None and slugify_part(".cache") == "cache" and slugify_part("") is None
    assert not is_valid_folder_name("..") and not is_valid_folder_name(".hidden") and not is_valid_folder_name("a/b")


def test_crawler_stores_subcategory_folders_tags_and_classification_source(tmp_path):
    api_file = py_code("g", "\nimport graphql\nclass Query: ...\n")
    fake = FakeGitHub(FakeRepo("o/r", files={"src/graphql/schema.py": api_file}))
    crawl(fake, tmp_path)
    names = [p.relative_to(tmp_path).as_posix() for p in files_on_disk(tmp_path)]
    assert names == ["api/graphql/o__r__src__graphql__schema.py"]
    store = Store(tmp_path)
    row = store.get_file("o/r", "src/graphql/schema.py")
    assert (row["category"], row["subcategory"], row["classified_by"]) == ("api", "graphql", "auto")
    assert "graphql" in json.loads(row["tags"])


# -- paramètres effectifs ---------------------------------------------------------------------------------


def run_cli(argv, capsys, fake=None):
    fake = fake or FakeGitHub(FakeRepo("o/r", files={"a.py": py_code("a")}))
    code = cli.main(argv, transport=fake.transport())
    out = capsys.readouterr()
    return code, out.out, out.err, fake


def base(tmp_path, *extra):
    return ["crawl", "--dest", str(tmp_path / "d"), "--language", "python", "--pause", "0",
            "--min-free-gb", "0", *extra]


def test_start_event_reports_the_parameters_really_used(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "A1b2C3d4E5" * 3 + "Zz9Yy8")
    argv = base(tmp_path, "--progress-json", "--min-stars", "1", "--max-repos", "500",
                "--max-files-per-repo", "40", "--max-files", "3000", "--max-kb", "200")
    code, out, _err, _ = run_cli(argv, capsys)
    assert code == 0
    start_event = json_lines(out)[0]
    config = start_event["config"]
    assert (config["min_stars"], config["max_repos"], config["max_files_per_repo"], config["max_files"]) == (1, 500, 40, 3000)
    assert config["max_file_kb"] == 200 and config["mode"] == "normal" and config["force"] is False
    done = json_lines(out)[-1]
    assert done["stats"]["stop_reason"] == "termine" and "stop_detail" in done["stats"]


def test_unsafe_cli_rejects_every_duration_and_size_cap(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "A1b2C3d4E5" * 3 + "Zz9Yy8")
    for extra in (["--max-minutes", "5"], ["--max-gb", "1"], ["--max-library-gb", "2"],
                  ["--stop-at", "2099-01-01T00:00:00+00:00"]):
        code, _out, err, _ = run_cli(base(tmp_path, "--unsafe", "--min-free-gb", "2", *extra), capsys)
        assert code == cli.EXIT_USAGE and "incompatible avec --unsafe" in err


def test_unsafe_requires_a_disk_guard(tmp_path, capsys):
    code, _out, err, _ = run_cli(base(tmp_path, "--unsafe"), capsys)        # --min-free-gb 0 : refusé
    assert code == cli.EXIT_USAGE and "garde disque" in err


def test_unsafe_start_event_has_no_caps(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "A1b2C3d4E5" * 3 + "Zz9Yy8")
    argv = base(tmp_path, "--progress-json", "--unsafe", "--min-free-gb", "2", "--max-repos", "3")
    code, out, _err, _ = run_cli(argv, capsys)
    event = json_lines(out)[0]
    assert code == 0 and event["config"]["mode"] == "unsafe"
    assert event["config"]["max_repos"] is None and event["config"]["max_files"] is None
    assert event["config"]["max_files_per_repo"] is None
    assert event["budget"]["max_seconds"] is None and event["budget"]["max_bytes"] is None
    assert event["budget"]["max_library_bytes"] is None and event["budget"]["min_free_bytes"] > 0


# -- mode UNSAFE ------------------------------------------------------------------------------------------


def test_normal_mode_respects_caps_but_unsafe_ignores_them(tmp_path):
    repos = many_repos(6, files=5)
    normal = crawl(PagedGitHub(*repos), tmp_path / "n", max_repos=2, max_files_per_repo=2, max_files=3)
    assert normal.files_saved == 3 and normal.stop_reason == "limite-fichiers"
    assert "3 fichiers au total" in normal.stop_detail

    unsafe = crawl(PagedGitHub(*repos), tmp_path / "u", unsafe=True, max_repos=2, max_files_per_repo=2, max_files=3)
    assert unsafe.stop_reason == "termine"
    assert unsafe.files_saved == 30 and unsafe.repos_done == 6
    assert len(files_on_disk(tmp_path / "u")) == 30


def test_unsafe_goes_beyond_the_1000_results_search_cap(tmp_path):
    fake = PagedGitHub(*many_repos(1150, first_stars=5000))
    stats = crawl(fake, tmp_path, unsafe=True)
    assert stats.repos_seen == 1150 and stats.files_saved == 1150
    assert stats.search_windows >= 1
    assert any(re.search(r"stars:100\.\.\d+", q) for q in fake.search_queries)


def test_normal_mode_stops_searching_at_max_repos(tmp_path):
    fake = PagedGitHub(*many_repos(300))
    stats = crawl(fake, tmp_path, max_repos=120)
    assert stats.repos_seen == 120 and stats.files_saved == 120
    assert len(fake.search_queries) == 2          # deux pages de 100 suffisent


def test_unsafe_still_excludes_secrets(tmp_path):
    secret = py_code("s", '\nTOKEN = "ghp_' + "a" * 36 + '"\n')
    fake = PagedGitHub(FakeRepo("o/r", files={"a.py": secret, "b.py": py_code("b")}))
    stats = crawl(fake, tmp_path, unsafe=True)
    assert stats.files_saved == 1 and stats.files_skipped["secret"] == 1


def test_unsafe_still_stops_on_low_disk(tmp_path):
    budget = make_budget(min_free_bytes=10 ** 18)           # plus que le disque
    stats = crawl(PagedGitHub(*many_repos(3)), tmp_path, unsafe=True, budget=budget)
    assert stats.stop_reason == "disque-presque-plein" and stats.files_saved == 0
    assert "espace disque" in stats.stop_detail


def test_unsafe_stops_on_manual_request(tmp_path):
    fake = PagedGitHub(*many_repos(5, files=3))
    run = start(fake, tmp_path, unsafe=True)
    fake.hook = lambda r: run.crawler.request_stop() if fake.count("raw") == 4 else None
    stats = run.crawler.run()
    assert stats.stop_reason == "arret-demande" and 0 < stats.files_saved < 15
    run.client.close(); run.store.close()


# -- raisons d'arrêt et reprises avec d'autres réglages ---------------------------------------------------


def test_raising_the_per_repo_quota_collects_the_rest(tmp_path):
    files = {f"src/m{i}.py": py_code(f"q{i}") for i in range(6)}
    fake = PagedGitHub(FakeRepo("o/r", files=files))
    first = crawl(fake, tmp_path, max_files_per_repo=2)
    assert first.files_saved == 2 and first.repos_quota_reached == 1

    again = crawl(fake, tmp_path, max_files_per_repo=2)           # même quota : rien à refaire
    assert again.repos_skipped == 1 and again.files_saved == 0

    more = crawl(fake, tmp_path, max_files_per_repo=5)            # quota plus haut : la suite est collectée
    assert more.repos_skipped == 0 and more.files_saved == 3
    assert len(files_on_disk(tmp_path)) == 5

    unlimited = crawl(fake, tmp_path, unsafe=True)
    assert unlimited.files_saved == 1 and len(files_on_disk(tmp_path)) == 6


def test_changing_file_filters_reexplores_a_done_repo(tmp_path):
    big = py_code("big", "\n" + "# remplissage\n" * 400)
    fake = PagedGitHub(FakeRepo("o/r", files={"a.py": py_code("a"), "big.py": big}))
    first = crawl(fake, tmp_path, filters=FilterConfig(max_bytes=2000, languages=frozenset({"python"})))
    assert first.files_saved == 1 and first.files_skipped["trop-gros"] == 1
    second = crawl(fake, tmp_path, filters=FilterConfig(max_bytes=60_000, languages=frozenset({"python"})))
    assert second.repos_skipped == 0 and second.files_saved == 1 and second.files_skipped["deja-enregistre"] == 1


def test_stop_detail_names_the_limit_reached(tmp_path):
    stats = crawl(PagedGitHub(*many_repos(3, files=4)), tmp_path, max_files=5)
    assert stats.stop_reason == "limite-fichiers" and stats.stop_detail == "5 fichiers au total"


# -- fichiers déjà téléchargés ----------------------------------------------------------------------------


def test_unchanged_blob_on_a_new_commit_is_not_downloaded(tmp_path):
    repo = FakeRepo("o/r", files={"a.py": py_code("a"), "b.py": py_code("b")})
    fake = PagedGitHub(repo)
    crawl(fake, tmp_path)
    repo.commit = "e" * 40
    repo.files["c.py"] = py_code("c")
    before = len(fake.raw_paths())
    stats = crawl(fake, tmp_path)
    assert fake.raw_paths()[before:] == ["c.py"]                  # seul le nouveau fichier est téléchargé
    assert stats.files_saved == 1 and stats.files_skipped["inchange"] == 2
    store = Store(tmp_path)
    assert store.get_file("o/r", "a.py")["commit_sha"] == "e" * 40       # provenance mise à jour


def test_modified_file_is_updated_without_duplicate_and_history_is_kept(tmp_path):
    repo = FakeRepo("o/r", files={"a.py": py_code("v1")})
    fake = PagedGitHub(repo)
    crawl(fake, tmp_path)
    (original,) = files_on_disk(tmp_path)
    repo.commit = "f" * 40
    repo.files["a.py"] = py_code("v2")
    stats = crawl(fake, tmp_path)
    assert stats.files_saved == 1 and stats.files_updated == 1
    assert files_on_disk(tmp_path) == [original] and original.read_bytes() == py_code("v2")
    store = Store(tmp_path)
    row = store.get_file("o/r", "a.py")
    assert row["sha256"] == hashlib.sha256(py_code("v2")).hexdigest() and row["commit_sha"] == "f" * 40
    (version,) = store.db.execute("SELECT * FROM file_versions").fetchall()
    assert bytes(version["content"]) == py_code("v1") and version["reason"] == "mise-a-jour-github"


def test_force_redownloads_compares_and_restores_missing_files(tmp_path):
    repo = FakeRepo("o/r", files={"a.py": py_code("a"), "b.py": py_code("b")})
    fake = PagedGitHub(repo)
    crawl(fake, tmp_path)
    gone = next(p for p in files_on_disk(tmp_path) if p.name.endswith("a.py"))
    gone.unlink()                                                  # fichier local perdu
    before = len(fake.raw_paths())

    normal = crawl(fake, tmp_path)                                 # sans force : dépôt à jour, rien ne bouge
    assert normal.repos_skipped == 1 and len(fake.raw_paths()) == before and not gone.exists()

    forced = crawl(fake, tmp_path, force=True)
    assert sorted(fake.raw_paths()[before:]) == ["a.py", "b.py"]   # tout est retéléchargé et comparé
    assert forced.files_skipped["inchange"] == 1                   # b.py identique : intact
    assert forced.files_saved == 1 and len(files_on_disk(tmp_path)) == 2   # a.py rétabli


def test_locally_deleted_file_is_not_collected_again_unless_forced(tmp_path):
    repo = FakeRepo("o/r", files={"a.py": py_code("a"), "b.py": py_code("b")})
    fake = PagedGitHub(repo)
    crawl(fake, tmp_path)
    store = Store(tmp_path)
    with store.db:
        store.db.execute("UPDATE files SET status = 'deleted' WHERE path = 'a.py'")
    next(p for p in files_on_disk(tmp_path) if p.name.endswith("a.py")).unlink()
    store.close()
    repo.commit = "d" * 40                                         # nouveau commit : le dépôt est réexploré

    stats = crawl(fake, tmp_path)
    assert stats.files_skipped["supprime-localement"] == 1 and len(files_on_disk(tmp_path)) == 1
    forced = crawl(fake, tmp_path, force=True)
    assert forced.files_saved == 1 and len(files_on_disk(tmp_path)) == 2


def test_locally_edited_file_survives_an_unchanged_upstream(tmp_path):
    repo = FakeRepo("o/r", files={"a.py": py_code("a")})
    fake = PagedGitHub(repo)
    crawl(fake, tmp_path)
    (local,) = files_on_disk(tmp_path)
    local.write_text("# modifié à la main\n", encoding="utf-8")
    repo.commit = "9" * 40
    crawl(fake, tmp_path, force=True)
    assert local.read_text(encoding="utf-8") == "# modifié à la main\n"


# -- arborescences tronquées -----------------------------------------------------------------------------


def test_truncated_tree_is_walked_folder_by_folder_and_skips_vendor_dirs(tmp_path):
    files = {f"src/pkg{i}/mod.py": py_code(f"m{i}") for i in range(4)}
    files["node_modules/x/lib.py"] = py_code("vendored")
    files["top.py"] = py_code("top")
    fake = PagedGitHub(FakeRepo("o/r", files=files), truncate_over=1)     # la réponse récursive n'en contient qu'un
    stats = crawl(fake, tmp_path, max_files_per_repo=50)
    assert stats.trees_truncated == 1 and stats.files_saved == 5            # rien n'est perdu
    assert not any("node_modules" in p.name for p in files_on_disk(tmp_path))
    tree_urls = [r.url.path for r in fake.log if "/git/trees/" in r.url.path]
    assert not any("node_modules" in url for url in tree_urls)             # dossier écarté : jamais parcouru


# -- migration du schéma ---------------------------------------------------------------------------------


V1 = """
CREATE TABLE repos (full_name TEXT PRIMARY KEY, commit_sha TEXT NOT NULL, license TEXT, stars INTEGER,
    status TEXT NOT NULL, files_saved INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE files (sha256 TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL, path TEXT NOT NULL,
    source_url TEXT NOT NULL, license TEXT, language TEXT NOT NULL, category TEXT NOT NULL, size INTEGER NOT NULL,
    dest_path TEXT NOT NULL, saved_at REAL NOT NULL);
"""


def test_v1_database_is_migrated_without_losing_rows(tmp_path):
    meta = tmp_path / ".crawler"
    meta.mkdir(parents=True)
    db = sqlite3.connect(meta / "crawler.sqlite3")
    db.executescript(V1)
    db.execute("INSERT INTO repos VALUES ('o/r', 'c1', 'MIT', 5, 'done', 2, 1.0)")
    rows = [("h1", "o/r", "c1", "a.py", "u", "MIT", "python", "api", 10, "api/o__r__a.py", 10.0),
            ("h2", "o/r", "c0", "a.py", "u", "MIT", "python", "api", 11, "api/o__r__a-2.py", 5.0),   # ancienne version
            ("h3", "o/r", "c1", "b.py", "u", "MIT", "python", "divers", 12, "divers/o__r__b.py", 11.0)]
    db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()

    store = Store(tmp_path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    migrated = {r["path"]: r for r in store.db.execute("SELECT * FROM files")}
    assert set(migrated) == {"a.py", "b.py"} and migrated["a.py"]["sha256"] == "h1"   # la plus récente est gardée
    assert migrated["a.py"]["local_sha256"] == "h1" and migrated["a.py"]["status"] == "active"
    assert store.repo_done("o/r", "c1")                                               # l'état des dépôts est conservé
    store.close()
    Store(tmp_path).close()                                                           # rouvrir ne migre pas deux fois


def test_fresh_database_has_the_v2_schema(tmp_path):
    store = Store(tmp_path)
    tables = {r["name"] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"repos", "files", "file_versions"} <= tables
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
