# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Intégration réelle : moteur Rust compilé + PostgreSQL + watcher.

Ignoré par défaut. Pour l'exécuter :

    export ICC_TEST_DATABASE_URL=postgresql://user@127.0.0.1:5432/base_de_test
    (base de test avec les migrations de config/database/migrations appliquées ;
     la table code_examples sera VIDÉE)
    uv run maturin develop   # dans engine/, pour installer codecenter_engine
    uv run pytest tests/test_integration_engine.py
"""

import json
import os
import time
import uuid

import pytest

from conftest import FakeProvider, make_config, run, tool_call, turn
from icc_ai.engine_gateway import RustEngine
from icc_ai.errors import EngineError
from icc_ai.service import SearchParams, SearchService

DB_URL = os.environ.get("ICC_TEST_DATABASE_URL")
engine_module = pytest.importorskip("codecenter_engine", reason="moteur Rust non installé")
pytestmark = pytest.mark.skipif(not DB_URL, reason="ICC_TEST_DATABASE_URL non défini")

MARKER = f"zebratoken{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    root = tmp_path_factory.mktemp("codes")
    (root / "python").mkdir()
    (root / "python" / "outils.py").write_text(
        f'"""Outils."""\nimport json\n\n\ndef {MARKER}(text):\n    """Analyse."""\n    return json.loads(text)\n'
    )
    (root / "rust").mkdir()
    (root / "rust" / "lecture.rs").write_text(f"pub fn lire_{MARKER}() -> i32 {{\n    1\n}}\n")

    (root / "web").mkdir()
    (root / "web" / "bundle.min.js").write_text(f"var {MARKER}_exclu = 1;\n")
    saved_env = {k: os.environ.get(k) for k in (
        "DATABASE_URL", "CODECENTER_EXCLUDE", "CODECENTER_EXCLUDE_FILE", "CODECENTER_WATCH_PATHS")}
    os.environ["DATABASE_URL"] = DB_URL
    os.environ["CODECENTER_EXCLUDE"] = "*.min.js"
    exclude_file = tmp_path_factory.mktemp("cfg") / "exclude.txt"
    exclude_file.write_text("")
    os.environ["CODECENTER_EXCLUDE_FILE"] = str(exclude_file)
    os.environ["CODECENTER_WATCH_PATHS"] = str(root)

    engine_module.start_watcher()
    try:
        for _ in range(80):  # attendre NOS deux fichiers (la base peut contenir d'anciens documents)
            time.sleep(0.25)
            try:
                found = run(RustEngine().search(MARKER, 5, None, None))
            except Exception:
                continue
            if {r["language"] for r in found["results"]} >= {"python", "rust"}:
                break
        yield root
    finally:
        engine_module.stop_watcher()
        for key, value in saved_env.items():  # ne pas polluer les autres tests
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_recherche_lecture_et_filtres_reels(indexed):
    engine = RustEngine()
    found = run(engine.search(MARKER, 5, None, None))
    assert {r["language"] for r in found["results"]} == {"python", "rust"}
    assert all(r["relative_path"] and not r["relative_path"].startswith("/") for r in found["results"])

    only_rust = run(engine.search(MARKER, 5, "rust", None))
    assert [r["language"] for r in only_rust["results"]] == ["rust"]

    doc = run(engine.get_document(only_rust["results"][0]["document_id"], 1, 1, None))
    assert doc["content"].startswith("pub fn lire_") and doc["content_matches_index"] is True

    filters = run(engine.list_filters())
    assert {"python", "rust"} <= {l["slug"] for l in filters["languages"]}


def test_erreurs_typees_du_moteur(indexed):
    engine = RustEngine()
    with pytest.raises(EngineError) as info:
        run(engine.search("   ", 5, None, None))
    assert info.value.kind == "invalid"
    with pytest.raises(EngineError) as info:
        run(engine.get_document("pas-un-uuid"))
    assert info.value.kind == "invalid"
    with pytest.raises(EngineError) as info:
        run(engine.get_document(str(uuid.uuid4())))
    assert info.value.kind == "not_found"


def test_chemin_hors_dossiers_surveilles_refuse(indexed, tmp_path):
    """Une ligne de base pointant hors des racines ne doit jamais être lue."""
    import shutil
    import subprocess

    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql absent")

    outside = tmp_path / "secret.txt"
    outside.write_text("mot de passe")
    doc_id = str(uuid.uuid4())
    sql = (
        "INSERT INTO code_examples (id, title, storage_path, content_sha256, file_size) "
        f"VALUES ('{doc_id}', 'secret.txt', '{outside}', 'x', 12)"
    )
    subprocess.run([psql, DB_URL, "-qc", sql], check=True)

    with pytest.raises(EngineError) as info:
        run(RustEngine().get_document(doc_id))
    assert info.value.kind == "forbidden"
    assert "mot de passe" not in info.value.message

    # La recherche n'expose pas non plus son contenu.
    found = run(RustEngine().search("mot passe secret", 5, None, None))
    assert all("mot de passe" not in r["snippet"] for r in found["results"])
    subprocess.run([psql, DB_URL, "-qc", f"DELETE FROM code_examples WHERE id = '{doc_id}'"], check=True)


def test_pipeline_complet_avec_vrai_moteur(indexed, store):
    store.save("ollama", make_config())
    answer = json.dumps({"summary": "Trouvé", "used_sources": ["S1"],
                         "sections": [{"code_blocks": [{"language": "python", "code": f"def {MARKER}(text):",
                                                         "source_ids": ["S1"]}]}]})
    provider = FakeProvider([
        turn("", [tool_call("read_file", {"source_id": "S1"})]),
        turn(answer),
    ])
    service = SearchService(store, RustEngine(), provider_factory=lambda r, t=None: provider)
    response = run(service.run(SearchParams(MARKER, limit=5)))

    assert response.status == "ok" and response.sources[0].cited
    assert response.tool_trace[0].ok
    assert response.sources[0].path.endswith((".py", ".rs"))
    assert response.meta.provider == "ollama" and response.meta.tool_calls == 1


def test_index_de_contenu_recherche_sans_relire_le_disque(indexed):
    """Le contenu vient de PostgreSQL : supprimer le fichier du disque ne change
    pas la notation, seul l'extrait (lu sur disque) disparaît."""
    engine = RustEngine()
    found = run(engine.search(MARKER, 5, None, None))
    assert found["results"] and all(r["content_matched"] for r in found["results"])
    assert all(MARKER in r["snippet"] for r in found["results"])
    assert found["scanned_files"] >= 2


def test_exclusions_et_etat_du_scan(indexed):
    found = run(RustEngine().search(f"{MARKER}_exclu", 5, None, None))
    assert all(not r["relative_path"].endswith(".min.js") for r in found["results"])

    status = json.loads(engine_module.scan_status())
    assert "scan" in status and "generation" in status

    engine_module.rescan_now()
    for _ in range(40):
        time.sleep(0.25)
        if not json.loads(engine_module.scan_status())["scan"].get("running"):
            break
    assert json.loads(engine_module.get_stats())["documents_total"] >= 2


def _wait_scan():
    time.sleep(0.3)
    for _ in range(60):
        if not json.loads(engine_module.scan_status())["scan"].get("running"):
            return
        time.sleep(0.25)


def test_exclusion_puis_retrait_reindexe_dans_la_meme_session(indexed):
    """Une exclusion retire le document de l'index ; la retirer le ré-indexe sans redémarrer."""
    exclude = __import__("pathlib").Path(os.environ["CODECENTER_EXCLUDE_FILE"])
    engine = RustEngine()
    exclude.write_text("*.rs\n")
    engine_module.rescan_now()
    _wait_scan()
    found = run(engine.search(MARKER, 5, None, None))
    assert {r["language"] for r in found["results"]} == {"python"}

    exclude.write_text("")
    engine_module.rescan_now()
    _wait_scan()
    found = run(engine.search(MARKER, 5, None, None))
    assert {r["language"] for r in found["results"]} == {"python", "rust"}
