# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Outils IA : validation stricte, limites, accès restreint aux sources."""

import pytest

from conftest import FakeEngine, run, tool_call
from icc_ai.sources import SourceRegistry
from icc_ai.tools import ToolContext, ToolLimits, build_default_registry


def setup(limits=None):
    engine = FakeEngine()
    ctx = ToolContext(engine, SourceRegistry(), limits or ToolLimits())
    return build_default_registry(), ctx, engine


def call(registry, ctx, name, args):
    return run(registry.execute(tool_call(name, args), ctx))


def test_outils_exposes_avec_schema_strict():
    registry, _, _ = setup()
    assert registry.names() == ["search_code", "read_file", "get_file_info", "list_filters"]
    for spec in registry.specs():
        assert spec.parameters["type"] == "object" and spec.parameters["additionalProperties"] is False
        assert "title" not in spec.parameters


def test_search_code_enregistre_les_sources():
    registry, ctx, engine = setup()
    out = call(registry, ctx, "search_code", {"query": "parse_json", "limit": 3})
    assert out.execution.ok and "[S1] codes/json_tools.py" in out.content
    assert ctx.sources.get("S1").document_id.startswith("1111")
    assert engine.calls[0] == ("search", "parse_json", 3, None, None)


def test_search_code_filtre_langage():
    registry, ctx, engine = setup()
    out = call(registry, ctx, "search_code", {"query": "fn read_all", "language": "rust"})
    assert "files.rs" in out.content and engine.calls[0][3] == "rust"


def test_search_code_sans_resultat():
    registry, ctx, _ = setup()
    out = call(registry, ctx, "search_code", {"query": "introuvable_zzz"})
    assert out.execution.ok and "Aucun résultat" in out.content


@pytest.mark.parametrize("args", [
    {}, {"query": ""}, {"query": "x"}, {"query": "ok", "limit": 99}, {"query": "ok", "extra": 1},
    {"query": "ok", "language": "../../etc"}, {"query": "a" * 400},
])
def test_arguments_invalides_refuses_sans_appeler_le_moteur(args):
    registry, ctx, engine = setup()
    out = call(registry, ctx, "search_code", args)
    assert not out.execution.ok and out.content.startswith("ERREUR : Arguments invalides")
    assert engine.calls == []


def test_outil_inconnu_et_arguments_illisibles():
    registry, ctx, _ = setup()
    assert "Outil inconnu" in call(registry, ctx, "rm_rf", {}).content
    bad = tool_call("search_code", {})
    bad.arguments_error = "Arguments d'outil non valides (JSON attendu)."
    assert not run(registry.execute(bad, ctx)).execution.ok


def test_read_file_exige_une_source_enregistree():
    registry, ctx, engine = setup()
    out = call(registry, ctx, "read_file", {"source_id": "S1"})
    assert not out.execution.ok and "inconnue" in out.content and engine.calls == []


@pytest.mark.parametrize("source_id", ["11111111-1111-1111-1111-111111111111", "../etc/passwd", "S0001", "s1", "S1; DROP"])
def test_read_file_refuse_tout_identifiant_non_serveur(source_id):
    registry, ctx, engine = setup()
    call(registry, ctx, "search_code", {"query": "parse_json"})
    n = len(engine.calls)
    out = call(registry, ctx, "read_file", {"source_id": source_id})
    assert not out.execution.ok and len(engine.calls) == n


def test_read_file_numerote_et_memorise_le_texte_vu():
    registry, ctx, _ = setup()
    call(registry, ctx, "search_code", {"query": "parse_json"})
    out = call(registry, ctx, "read_file", {"source_id": "S1", "start_line": 4, "end_line": 6})
    assert "lignes 4-6 sur" in out.content and "5| def parse_json(text):" in out.content
    assert "def parse_json(text):" in ctx.sources.get("S1").seen_text


def test_read_file_plage_incoherente():
    registry, ctx, _ = setup()
    call(registry, ctx, "search_code", {"query": "parse_json"})
    out = call(registry, ctx, "read_file", {"source_id": "S1", "start_line": 9, "end_line": 2})
    assert not out.execution.ok


def test_read_file_erreur_moteur_renvoyee_au_modele():
    registry, ctx, engine = setup()
    call(registry, ctx, "search_code", {"query": "parse_json"})
    engine.fail_documents.add(ctx.sources.get("S1").document_id)
    out = call(registry, ctx, "read_file", {"source_id": "S1"})
    assert not out.execution.ok and "dossiers autorisés" in out.content


def test_resultat_tronque_aux_limites():
    registry, ctx, _ = setup(ToolLimits(max_result_chars=120))
    call(registry, ctx, "search_code", {"query": "parse_json"})
    out = call(registry, ctx, "read_file", {"source_id": "S1"})
    assert len(out.content) < 200 and "tronqué" in out.content and out.execution.result_chars == len(out.content)


def test_get_file_info_sans_code():
    registry, ctx, engine = setup()
    call(registry, ctx, "search_code", {"query": "parse_json"})
    out = call(registry, ctx, "get_file_info", {"source_id": "S1"})
    assert '"symboles":[{"name":"parse_json"}]' in out.content and "return json.loads" not in out.content
    assert engine.calls[-1][4] == 1  # lecture minimale


def test_list_filters():
    registry, ctx, _ = setup()
    out = call(registry, ctx, "list_filters", {})
    assert "python (1)" in out.content and "json (1)" in out.content


def test_bug_d_outil_ne_plante_pas(monkeypatch):
    registry, ctx, engine = setup()

    async def boom(*a, **k):
        raise RuntimeError("secret interne")

    monkeypatch.setattr(engine, "search", boom)
    out = call(registry, ctx, "search_code", {"query": "parse_json"})
    assert not out.execution.ok and "secret interne" not in out.content


def test_enregistrement_en_double_refuse():
    registry, _, _ = setup()
    with pytest.raises(ValueError):
        registry.register(registry._tools["search_code"])
