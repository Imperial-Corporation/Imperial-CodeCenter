import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import TOKEN, files_on_disk
from icc_crawler.storage import MAX_NAME, Store, safe_filename


@pytest.mark.parametrize("repo,path", [
    ("o/r", "../../etc/passwd.py"),
    ("o/r", "a/../../b.py"),
    ("o/r", "/etc/shadow.py"),
    ("o/r", "..\\..\\windows\\x.py"),
    ("o/r", ".hidden/.bashrc"),
    ("o/r", ".env.py"),
    ("o/r", "dossier/.gitignore"),
    ("o/r", "a\x00b\nc\rd\te.py"),
    ("o/r", "é/日本語/ça.py"),
    ("o/r", "con:sole|x?*<>\"y.py"),
    ("o/r", "x/" * 80 + "f.py"),
    ("../..", "a.py"),
    (".hidden/.repo", "a.py"),
    ("o/r", "a." + "b" * 300),
    ("o/r", "noext"),
    ("o/r", ""),
    ("o/r", "..."),
])
def test_safe_filename_is_always_safe(repo, path):
    name = safe_filename(repo, path)
    assert name and len(name) <= MAX_NAME
    assert "/" not in name and "\\" not in name and ".." not in name
    assert not name.startswith(".") and not name.startswith("-")
    assert all(ch.isascii() and (ch.isalnum() or ch in "._-") for ch in name)
    assert Path(name).name == name


def test_safe_filename_shape():
    assert safe_filename("owner/repo", "src/app.py") == "owner__repo__src__app.py"
    assert safe_filename("owner/repo", "Makefile.PY") == "owner__repo__Makefile.PY"


def test_safe_filename_unique_and_case_insensitive():
    taken: set[str] = set()
    names = [safe_filename("o/r", "A.py", taken), safe_filename("o/r", "a.py", taken),
             safe_filename("o/r", "A.py", taken)]
    assert len({n.lower() for n in names}) == 3
    assert names[0] == "o__r__A.py" and names[1].endswith("-2.py")


def test_safe_filename_collapses_distinct_paths_without_collision():
    taken: set[str] = set()
    a = safe_filename("o/r", "a/b.py", taken)
    b = safe_filename("o/r", "a__b.py", taken)
    c = safe_filename("o/r", "a b.py", taken)
    assert len({a, b, c}) == 3


def test_long_names_are_truncated_deterministically_and_stay_distinct():
    p1, p2 = "d/" * 100 + "x.py", "d/" * 100 + "y.py"
    n1, n2 = safe_filename("o/r", p1), safe_filename("o/r", p2)
    assert len(n1) <= MAX_NAME and n1 != n2 and n1.endswith(".py")
    assert safe_filename("o/r", p1) == n1


def save(store, data=b"print('x')\n", **kw):
    args = dict(repo="o/r", commit_sha="c1", path="src/a.py", license_id="MIT",
                language="python", category="divers", data=data)
    args.update(kw)
    return store.save_file(**args)


def test_save_file_writes_exact_bytes_including_bom_and_crlf(tmp_path):
    store = Store(tmp_path)
    data = b"\xef\xbb\xbfprint('caf\xc3\xa9')\r\nx = 1\r\n"
    target, written = save(store, data)
    assert target.read_bytes() == data and written == len(data)
    assert target == tmp_path / "divers" / "o__r__src__a.py"
    assert not list(tmp_path.rglob("*.part"))


def test_known_content_is_not_written_again(tmp_path):
    store = Store(tmp_path)
    save(store)
    assert save(store, path="src/copie.py", repo="autre/depot") == (None, 0)
    assert len(files_on_disk(tmp_path)) == 1 and store.stats()["files"] == 1


def test_dedup_does_not_depend_on_original_location(tmp_path):
    """Le moteur Rust déplace les fichiers : l'état SQLite seul fait foi."""
    store = Store(tmp_path)
    target, _ = save(store, commit_sha="c1")
    (tmp_path / "divers" / "python").mkdir()
    moved = tmp_path / "divers" / "python" / target.name
    target.rename(moved)

    digest = hashlib.sha256(b"print('x')\n").hexdigest()
    assert store.known_hash(digest)
    assert store.has_path("o/r", "c1", "src/a.py")
    assert not store.has_path("o/r", "c2", "src/a.py")
    assert save(store, commit_sha="c2") == (None, 0)          # même contenu : rien recréé
    assert not target.exists()                                # pas de réapparition à l'ancien emplacement

    # Un contenu différent au même chemin ne réutilise pas le nom déjà attribué.
    other, _ = save(store, b"print('y')\n", commit_sha="c2")
    assert other.name != target.name and other.name == "o__r__src__a-2.py"


def test_names_survive_reopening_the_store(tmp_path):
    store = Store(tmp_path)
    first, _ = save(store)
    first.unlink()                                            # le moteur l'a emporté ailleurs
    store.close()
    store = Store(tmp_path)
    second, _ = save(store, b"autre contenu\n")
    assert second.name == "o__r__src__a-2.py"


def test_orphan_file_with_same_content_is_adopted(tmp_path):
    (tmp_path / "divers").mkdir(parents=True)
    orphan = tmp_path / "divers" / "o__r__src__a.py"
    orphan.write_bytes(b"print('x')\n")                       # écrit avant une interruption, jamais référencé
    store = Store(tmp_path)
    target, written = save(store)
    assert target == orphan and written == 0 and store.stats()["files"] == 1
    assert len(files_on_disk(tmp_path)) == 1


def test_existing_foreign_file_is_never_overwritten(tmp_path):
    (tmp_path / "divers").mkdir(parents=True)
    foreign = tmp_path / "divers" / "o__r__src__a.py"
    foreign.write_bytes(b"fichier de l'utilisateur\n")
    store = Store(tmp_path)
    target, _ = save(store)
    assert foreign.read_bytes() == b"fichier de l'utilisateur\n"
    assert target.name == "o__r__src__a-2.py"


def test_category_escaping_destination_is_refused(tmp_path):
    outside = tmp_path / "dehors"
    outside.mkdir()
    dest = tmp_path / "dest"
    store = Store(dest)
    (dest / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        save(store, category="evil")
    with pytest.raises(ValueError):
        save(store, category="../dehors")
    assert not any(outside.iterdir())


def test_write_failure_leaves_no_partial_file_and_no_db_row(tmp_path, monkeypatch):
    store = Store(tmp_path)

    def broken(src, dst):
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        save(store)
    monkeypatch.undo()
    assert files_on_disk(tmp_path) == [] and not list(tmp_path.rglob(".*.part"))
    assert store.stats()["files"] == 0
    assert save(store)[0] is not None                          # réessai possible


def test_db_failure_removes_written_file(tmp_path, monkeypatch):
    store = Store(tmp_path)
    save(store, b"un\n")
    # Même sha déjà connu ne passe pas par l'écriture ; on force une collision de clé après coup.
    original = store.known_hash
    monkeypatch.setattr(store, "known_hash", lambda digest: False)
    with pytest.raises(sqlite3.IntegrityError):
        save(store, b"un\n", path="autre.py")
    monkeypatch.setattr(store, "known_hash", original)
    assert len(files_on_disk(tmp_path)) == 1


def test_repo_state_machine(tmp_path):
    store = Store(tmp_path)
    assert not store.repo_done("o/r", "c1")
    store.mark_repo("o/r", "c1", "MIT", 10, "interrupted", 2)
    assert not store.repo_done("o/r", "c1")
    store.mark_repo("o/r", "c1", "MIT", 10, "done", 3)
    assert store.repo_done("o/r", "c1")
    assert not store.repo_done("o/r", "c2")                    # nouveau commit : à revisiter
    assert store.stats()["repos"] == 1


def test_state_persists_between_instances(tmp_path):
    store = Store(tmp_path)
    save(store)
    store.mark_repo("o/r", "c1", "MIT", 1, "done", 1)
    store.close()
    again = Store(tmp_path)
    assert again.repo_done("o/r", "c1") and again.has_path("o/r", "c1", "src/a.py")


def test_library_size_and_stats(tmp_path):
    store = Store(tmp_path)
    save(store, b"a" * 10)
    save(store, b"b" * 20, path="b.py", category="api", language="python")
    save(store, b"c" * 5, path="c.rs", language="rust")
    stats = store.stats()
    assert stats["files"] == 3 and stats["bytes"] == 35 == store.library_size()
    assert stats["by_category"] == {"divers": 2, "api": 1}
    assert stats["by_language"] == {"python": 2, "rust": 1}


def test_save_license_is_kept_outside_indexed_tree(tmp_path):
    store = Store(tmp_path)
    store.save_license("owner/repo", "MIT License\nTexte", "MIT")
    store.save_license("owner/sans", None, "Apache-2.0")
    assert (tmp_path / ".crawler" / "licenses" / "owner__repo.txt").read_text(encoding="utf-8") == "MIT License\nTexte"
    assert "Apache-2.0" in (tmp_path / ".crawler" / "licenses" / "owner__sans.txt").read_text(encoding="utf-8")
    assert files_on_disk(tmp_path) == []


def test_save_license_name_is_sanitised(tmp_path):
    store = Store(tmp_path)
    store.save_license("../../evil/x", "t", "MIT")
    created = list((tmp_path / ".crawler" / "licenses").iterdir())
    assert len(created) == 1 and ".." not in created[0].name and created[0].parent.name == "licenses"
    assert not (tmp_path.parent / "evil").exists()


def test_manifest_is_jsonl_with_provenance_and_no_secrets(tmp_path):
    store = Store(tmp_path)
    save(store, b"un\n", repo="o/a", path="z.py")
    save(store, b"deux\n", repo="o/a", path="a.py", language="python")
    out = tmp_path / "manifeste.jsonl"
    assert store.export_manifest(out) == 2
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["path"] for r in rows] == ["a.py", "z.py"]
    assert rows[0]["source_url"] == "https://github.com/o/a/blob/c1/a.py"
    assert rows[0]["license"] == "MIT" and rows[0]["sha256"] == hashlib.sha256(b"deux\n").hexdigest()
    assert TOKEN not in out.read_text(encoding="utf-8")
    assert {"repo", "commit_sha", "dest_path", "category", "size", "language"} <= set(rows[0])


def test_empty_manifest(tmp_path):
    store = Store(tmp_path)
    assert store.export_manifest(tmp_path / "m.jsonl") == 0
    assert (tmp_path / "m.jsonl").read_text() == ""
