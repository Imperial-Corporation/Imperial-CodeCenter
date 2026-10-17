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

    os.environ["DATABASE_URL"] = DB_URL
    os.environ["CODECENTER_WATCH_PATHS"] = str(root)

    engine_module.start_watcher()
    try:
        for _ in range(40):
            time.sleep(0.5)
            if json.loads(engine_module.get_stats())["documents_total"] >= 2:
                break
        yield root
    finally:
        engine_module.stop_watcher()


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
