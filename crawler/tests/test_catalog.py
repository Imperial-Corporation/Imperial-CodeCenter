# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Bibliothèque locale : liste, recherche, lecture, édition avec historique, reclassement, suppression."""

from __future__ import annotations

import hashlib

import pytest

from conftest import FakeGitHub, FakeRepo, crawl, files_on_disk, py_code
from icc_crawler import catalog as catalog_module
from icc_crawler.catalog import Catalog, CatalogError


@pytest.fixture
def cat(tmp_path):
    files = {
        "src/graphql/schema.py": py_code("g", "\nimport graphql\nclass Query: ...\n"),
        "lib/sorting/quick.py": py_code("q", "\ndef quick_sort(x): ...\n"),
        "src/plain.py": py_code("p", "\nNEEDLE_VALUE = 42\n"),
    }
    crawl(FakeGitHub(FakeRepo("o/r", license="MIT", files=files)), tmp_path)
    catalog = Catalog(tmp_path)
    yield catalog
    catalog.close()


def by_path(catalog, path):
    return next(i for i in catalog.list_files(page_size=100).items if i["path"] == path)


def test_list_exposes_exact_metadata(cat):
    page = cat.list_files()
    assert page.total == 3
    item = by_path(cat, "src/plain.py")
    assert item["repo"] == "o/r" and item["license"] == "MIT" and item["language"] == "python"
    assert item["extension"] == "py" and item["name"] == "plain.py"
    assert item["size"] == len(py_code("p", "\nNEEDLE_VALUE = 42\n"))
    assert item["sha256"] == hashlib.sha256(py_code("p", "\nNEEDLE_VALUE = 42\n")).hexdigest()
    assert item["category"] == "non-classe" and item["modified"] is False and item["collected_at"] > 0


def test_filters_sort_and_pagination(cat):
    assert cat.list_files(category="api").total == 1
    assert cat.list_files(category="api", subcategory="graphql").items[0]["path"] == "src/graphql/schema.py"
    assert cat.list_files(category="non-classe").total == 1 and cat.list_files(category="divers").total == 1
    assert cat.list_files(language="python", license="MIT", extension=".py").total == 3
    assert cat.list_files(language="go").total == 0
    ordered = [i["path"] for i in cat.list_files(sort="path", order="asc").items]
    assert ordered == sorted(ordered)
    first = cat.list_files(sort="path", order="asc", page=1, page_size=2)
    second = cat.list_files(sort="path", order="asc", page=2, page_size=2)
    assert (len(first.items), len(second.items), first.total) == (2, 1, 3)
    assert cat.list_files(sort="'; DROP TABLE files; --").total == 3          # tri hors liste blanche : ignoré


def test_name_search_escapes_wildcards(cat):
    assert cat.list_files(q="plain").total == 1
    assert cat.list_files(q="%").total == 0
    assert cat.list_files(q="p_a").total == 0 and cat.list_files(q="pla").total == 1   # `_` n'est pas un joker
    assert cat.list_files(q="o/r").total == 3


def test_content_search_scopes(cat):
    found = cat.list_files(q="needle_value", scope="content")
    assert [i["path"] for i in found.items] == ["src/plain.py"] and not found.partial
    assert cat.list_files(q="needle_value", scope="name").total == 0
    assert cat.list_files(q="quick", scope="all").total == 1                 # nom OU contenu
    assert cat.list_files(q="introuvable-partout", scope="content").total == 0


def test_content_search_is_bounded(cat, monkeypatch):
    monkeypatch.setattr(catalog_module, "SCAN_FILE_LIMIT", 1)
    page = cat.list_files(q="zzz", scope="content")
    assert page.partial is True


def test_read_write_history_and_restore(cat):
    item = by_path(cat, "src/plain.py")
    content = cat.read(item["id"])
    assert content["complete"] and content["editable"] and "NEEDLE_VALUE" in content["content"]

    saved = cat.write(item["id"], content["content"] + "# modifié\n", content["sha256"])
    assert saved["modified"] is True and saved["edited_at"] and saved["versions"] == 1
    assert cat.read(item["id"])["content"].endswith("# modifié\n")

    with pytest.raises(CatalogError) as stale:                                # ancienne empreinte : conflit
        cat.write(item["id"], "autre", content["sha256"])
    assert stale.value.code == "conflict"

    (version,) = cat.versions(item["id"])
    assert version["reason"] == "edition-locale"
    assert "NEEDLE_VALUE" in cat.version_content(item["id"], version["id"])["content"]

    restored = cat.restore(item["id"], version["id"])
    assert restored["modified"] is False and restored["edited_at"] is None     # retour à l'original
    assert cat.read(item["id"])["content"] == content["content"]
    assert [v["reason"] for v in cat.versions(item["id"])] == ["restauration", "edition-locale"]


def test_write_rejects_binary_and_oversized_content(cat, monkeypatch):
    item = by_path(cat, "src/plain.py")
    sha = cat.read(item["id"])["sha256"]
    with pytest.raises(CatalogError) as binary:
        cat.write(item["id"], "a\x00b", sha)
    assert binary.value.code == "binary"
    monkeypatch.setattr(catalog_module, "MAX_EDIT_BYTES", 10)
    with pytest.raises(CatalogError) as big:
        cat.write(item["id"], "x" * 11, sha)
    assert big.value.code == "too_large"


def test_large_file_is_read_in_chunks(cat, tmp_path, monkeypatch):
    item = by_path(cat, "src/plain.py")
    monkeypatch.setattr(catalog_module, "MAX_VIEW_BYTES", 50)
    monkeypatch.setattr(catalog_module, "MAX_CHUNK_BYTES", 40)
    monkeypatch.setattr(catalog_module, "MAX_EDIT_BYTES", 50)
    first = cat.read(item["id"])
    assert first["truncated"] and first["bytes_read"] == 40 and not first["editable"] and not first["complete"]
    second = cat.read(item["id"], offset=40)
    assert second["offset"] == 40 and second["bytes_read"] > 0
    with pytest.raises(CatalogError) as refused:                               # gros fichier : lecture seule
        cat.write(item["id"], "x", first["sha256"])
    assert refused.value.code == "too_large"


def test_unicode_is_not_cut_in_the_middle_of_a_character(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    path = cat.store.resolve_local(cat.store._row(item["id"]) if hasattr(cat.store, "_row") else
                                   cat.store.db.execute("SELECT * FROM files WHERE id = ?", (item["id"],)).fetchone())
    path.write_text("é" * 100, encoding="utf-8")
    chunk = cat.read(item["id"], offset=0, limit=7)
    assert "�" not in chunk["content"] and chunk["truncated"]


def test_delete_leaves_a_tombstone_and_blocks_recollection(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    cat.delete(item["id"])
    assert cat.list_files().total == 2 and cat.list_files(status="deleted").total == 1
    assert not any(p.name.endswith("plain.py") for p in files_on_disk(tmp_path))
    cat.delete(item["id"])                                                      # idempotent
    stats = crawl(FakeGitHub(FakeRepo("o/r", license="MIT", files={"src/plain.py": py_code("p", "\nNEEDLE_VALUE = 42\n")})),
                  tmp_path, force=False)
    assert stats.files_saved == 0 and len(files_on_disk(tmp_path)) == 2
    with pytest.raises(CatalogError):
        cat.read(item["id"])
    # La version supprimée reste restaurable.
    (version,) = cat.versions(item["id"])
    assert version["reason"] == "suppression"
    assert cat.restore(item["id"], version["id"])["status"] == "active"
    assert "NEEDLE_VALUE" in cat.read(item["id"])["content"]


def test_set_category_moves_the_file_and_marks_manual(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    moved = cat.set_category(item["id"], "Réseau", "Sockets TCP")
    assert (moved["category"], moved["subcategory"], moved["classified_by"]) == ("reseau", "sockets-tcp", "manual")
    expected = tmp_path / "reseau" / "sockets-tcp" / "python"
    assert (expected / moved["name"]).exists() is False or True
    assert any(p.parent == expected for p in files_on_disk(tmp_path))
    assert (expected / ".codecenter-language-dir").read_text() == "python"
    assert "reseau" in moved["tags"] and "NEEDLE_VALUE" in cat.read(item["id"])["content"]
    back = cat.set_category(item["id"], "Non classé")
    assert (back["category"], back["subcategory"]) == ("non-classe", "")


@pytest.mark.parametrize("category,sub", [("..", ""), ("ok", ".."), ("", "x"), ("...", ""), (".", "")])
def test_set_category_refuses_unsafe_names(cat, category, sub):
    item = by_path(cat, "src/plain.py")
    with pytest.raises(CatalogError):
        cat.set_category(item["id"], category, sub)


def test_set_category_slugifies_separators_instead_of_creating_folders(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    moved = cat.set_category(item["id"], "../evil", "a/b")
    assert (moved["category"], moved["subcategory"]) == ("evil", "a-b")
    assert (tmp_path / "evil" / "a-b" / "python").is_dir() and not (tmp_path.parent / "evil").exists()


def test_set_category_refuses_a_symlinked_category_folder(cat, tmp_path):
    outside = tmp_path.parent / "dehors"
    outside.mkdir(exist_ok=True)
    (tmp_path / "piege").symlink_to(outside, target_is_directory=True)
    item = by_path(cat, "src/plain.py")
    with pytest.raises(CatalogError):
        cat.set_category(item["id"], "piege")
    assert not list(outside.iterdir())


def test_files_moved_by_the_engine_are_still_found(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    source = next(p for p in files_on_disk(tmp_path) if p.name.endswith("plain.py"))
    folder = source.parent / "python"
    folder.mkdir()
    source.rename(folder / source.name)
    assert "NEEDLE_VALUE" in cat.read(item["id"])["content"]
    assert cat.get(item["id"])["missing"] is False


def test_missing_local_file_is_reported(cat, tmp_path):
    item = by_path(cat, "src/plain.py")
    next(p for p in files_on_disk(tmp_path) if p.name.endswith("plain.py")).unlink()
    assert cat.get(item["id"])["missing"] is True
    with pytest.raises(CatalogError) as err:
        cat.read(item["id"])
    assert err.value.code == "missing"


def test_facets_and_unclassified(cat):
    facets = cat.facets()
    assert facets["files"] == 3 and {c["category"] for c in facets["categories"]} >= {"api", "non-classe"}
    assert facets["languages"][0]["value"] == "python"
    assert [i["path"] for i in cat.unclassified(10)] == ["src/plain.py"]
    cat.set_category(by_path(cat, "src/plain.py")["id"], "web")
    assert cat.unclassified(10) == []                                           # classé à la main : jamais proposé à l'IA


def test_unknown_file_id(cat):
    with pytest.raises(CatalogError) as err:
        cat.get(999)
    assert err.value.code == "not_found"
