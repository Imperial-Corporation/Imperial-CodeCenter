# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Validation de la réponse du modèle et séparation contenu / métadonnées."""

import json

from icc_ai.response import build_answer, parse_model_output, sources_out
from icc_ai.sources import SourceRegistry


def registry_with(text="def parse_json(text):\n    return json.loads(text)"):
    registry = SourceRegistry()
    source = registry.register_hit({
        "document_id": "d1", "title": "t", "relative_path": "codes/a.py", "language": "python",
        "language_name": "Python", "content_sha256": "abc", "score": 2.0,
        "line_start": 1, "line_end": 2, "snippet": text,
    })
    return registry, source


GOOD = {
    "summary": "Utilise json.loads.",
    "sections": [{"title": "Code", "body": "Voici.", "code_blocks": [
        {"language": "Python", "code": "def parse_json(text):\n    return json.loads(text)", "source_ids": ["S1"]}]}],
    "used_sources": ["S1"],
}


def test_json_valide():
    answer, warnings = parse_model_output(json.dumps(GOOD))
    assert answer.summary == "Utilise json.loads." and not warnings


def test_json_entoure_de_cloture_et_de_texte():
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    assert parse_model_output(fenced)[0].summary
    chatty = "Voici ma réponse : " + json.dumps(GOOD) + " Bonne journée !"
    assert parse_model_output(chatty)[0].summary


def test_format_incorrect_ne_plante_pas_texte_libre_et_code_extrait():
    raw = "Réponse libre.\n```python\nprint('x')\n```\nFin."
    answer, warnings = parse_model_output(raw)
    assert "Réponse libre." in answer.summary and warnings
    assert answer.sections[0].code_blocks[0].code == "print('x')"
    assert answer.sections[0].code_blocks[0].language == "python"


def test_json_de_mauvaise_forme_retombe_sur_le_texte():
    answer, warnings = parse_model_output('{"summary": 12, "sections": "non"}')
    assert warnings and answer.summary


def test_sortie_demesuree_rejetee_puis_texte_libre_borne():
    huge = json.dumps({"summary": "x" * 10_000})
    answer, warnings = parse_model_output(huge)
    assert warnings and len(answer.summary) <= 4000


def test_identifiants_inventes_retires_avec_avertissement():
    registry, _ = registry_with()
    model, _ = parse_model_output(json.dumps({**GOOD, "used_sources": ["S1", "S99", "../../etc/passwd"]}))
    answer, cited, warnings = build_answer(model, registry)
    assert cited == {"S1"} and any("absentes des résultats réels" in w for w in warnings)


def test_code_verifie_uniquement_si_present_dans_la_source():
    registry, _ = registry_with()
    invented = {**GOOD, "sections": [{"title": "", "body": "", "code_blocks": [
        {"language": "python", "code": "import os\nos.system('x')", "source_ids": ["S1"]},
        {"language": "python", "code": "def parse_json(text):\n    return json.loads(text)", "source_ids": ["S1"]},
        {"language": "python", "code": "def parse_json(text):\n    return json.loads(text)", "source_ids": []},
    ]}]}
    model, _ = parse_model_output(json.dumps(invented))
    answer, _, _ = build_answer(model, registry)
    verified = [b.verified for b in answer.sections[0].code_blocks]
    assert verified == [False, True, False]


def test_langage_du_bloc_assaini():
    registry, _ = registry_with()
    data = {**GOOD, "sections": [{"code_blocks": [{"language": "<script>", "code": "x"}]}]}
    answer, _, _ = build_answer(parse_model_output(json.dumps(data))[0], registry)
    assert answer.sections[0].code_blocks[0].language == ""


def test_aucune_source_citee_avertit():
    registry, _ = registry_with()
    model, _ = parse_model_output(json.dumps({"summary": "ok"}))
    _, cited, warnings = build_answer(model, registry)
    assert not cited and any("aucune source" in w for w in warnings)


def test_sources_viennent_du_serveur_citees_d_abord():
    registry, _ = registry_with()
    registry.register_hit({"document_id": "d2", "title": "b", "relative_path": "codes/b.py",
                           "language": "python", "score": 1.0})
    out = sources_out(registry, {"S2"})
    assert [s.id for s in out] == ["S2", "S1"] and out[0].cited and not out[1].cited
    assert out[1].excerpts[0].text.startswith("def parse_json")
