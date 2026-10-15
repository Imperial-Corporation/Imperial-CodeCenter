"""Compression sémantique : niveaux, budget, repli, doublons, honnêteté."""

from icc_ai.compression import (
    FULL, OUTLINE, REFERENCE, SELECTED, DocInput, analyze, compress_documents, estimate_tokens,
)
from icc_ai.sources import Source


def source(n, language="python", path=None, sha=None, summary=None):
    return Source(id=f"S{n}", document_id=f"id{n}", title=f"f{n}", path=path or f"codes/f{n}.py",
                  language=language, language_name=language, categories=[],
                  sha256=sha or f"sha{n}" * 4, score=1.0, summary=summary)


def big_python(n_funcs=40):
    parts = ['"""Module."""', "import os", "import json", ""]
    for i in range(n_funcs):
        parts += [f"def fonction_{i}(x):", f'    """Fait la chose {i}."""',
                  *(f"    y{j} = x + {j}" for j in range(8)), "    return x", ""]
    parts += ["def parse_json(text):", '    """Analyse du JSON."""', "    return json.loads(text)", ""]
    return "\n".join(parts)


def test_analyse_python_symboles_et_imports():
    outline = analyze("python", big_python(2))
    names = [s.name for s in outline.symbols]
    assert names == ["fonction_0", "fonction_1", "parse_json"]
    assert outline.imports == ["os", "json"] and outline.header == ["Module."]


def test_analyse_rust_et_javascript():
    rust = analyze("rust", "use std::fs;\npub fn a() {\n  1\n}\nstruct S {\n x: i32\n}\n")
    assert [s.name for s in rust.symbols] == ["a", "S"] and rust.imports == ["std::fs"]
    js = analyze("javascript", "import x from 'lib';\nexport async function f() {\n return 1;\n}\nconst g = (a) => a;\n")
    assert [s.name for s in js.symbols] == ["f", "g"] and js.imports == ["lib"]


def test_fichier_court_reste_complet_avec_en_tete_honnete():
    doc = DocInput(source(1), "def a():\n    return 1\n")
    result = compress_documents([doc], ["a"], 5000)
    block = result.blocks[0]
    assert block.level == FULL and "COMPLET (lignes 1-2 sur 2)" in block.text
    assert not result.warnings and "1| def a():" in block.text


def test_gros_fichier_est_compresse_sans_perdre_le_symbole_pertinent():
    content = big_python()
    doc = DocInput(source(1), content)
    budget = estimate_tokens(content) // 5
    result = compress_documents([doc], ["parse_json"], budget)
    block = result.blocks[0]

    assert block.level in (SELECTED, OUTLINE)
    assert block.tokens <= budget
    assert "return json.loads(text)" in block.text          # corps pertinent conservé
    assert "corps omis" in block.text                        # les autres sont résumés
    assert "dépendances : os, json" in block.text            # dépendances conservées
    assert "read_file" in block.text                         # renvoi vers l'original
    assert "COMPLET" not in block.text.splitlines()[0]       # jamais présenté comme intégral
    assert any("compressés" in w for w in result.warnings)


def test_budget_respecte_pour_plusieurs_fichiers_les_mieux_classes_gardent_du_detail():
    docs = [DocInput(source(i, path=f"codes/f{i}.py", sha=f"s{i}"), big_python(25)) for i in range(1, 6)]
    budget = 2500
    result = compress_documents(docs, ["parse_json"], budget)
    assert result.used_tokens <= budget
    levels = [b.level for b in result.blocks]
    assert levels == sorted(levels)  # le moins bien classé est le plus dégradé


def test_budget_minuscule_degrade_en_reference_puis_retire_les_derniers():
    docs = [DocInput(source(i, sha=f"s{i}"), big_python(10), ) for i in range(1, 7)]
    result = compress_documents(docs, ["parse_json"], 150)
    assert result.dropped and any("non jointes" in w for w in result.warnings)
    assert all(b.level == REFERENCE for b in result.blocks)
    assert len(result.blocks) >= 1


def test_repli_sur_extraits_si_analyse_impossible():
    broken = "def oups(:\n" + "\n".join(f"ligne {i}" for i in range(300)) + "\ncible_unique ici\n"
    doc = DocInput(source(1), broken)
    result = compress_documents([doc], ["cible_unique"], estimate_tokens(broken) // 4)
    block = result.blocks[0]
    assert block.fallback and "cible_unique" in block.text
    assert any("repli" in w for w in result.warnings)


def test_langage_inconnu_utilise_le_repli():
    text = "\n".join(f"x{i}" for i in range(500)) + "\nbingo\n"
    result = compress_documents([DocInput(source(1, language="cobol"), text)], ["bingo"], 200)
    assert result.blocks[0].fallback and "bingo" in result.blocks[0].text


def test_doublons_par_empreinte_ne_sont_pas_repetes():
    a = DocInput(source(1, sha="same"), "def a():\n    return 1\n")
    b = DocInput(source(2, path="copie/a.py", sha="same"), "def a():\n    return 1\n")
    result = compress_documents([a, b], ["a"], 5000)
    assert [x.source_id for x in result.blocks] == ["S1"]
    assert "identique à : S2" in result.blocks[0].text


def test_corps_identiques_entre_fichiers_differents_sont_references():
    shared = "def commun(x):\n    return x * 2 + 1\n"
    a = DocInput(source(1, sha="a"), shared + "\n" + "\n".join(f"def f{i}():\n    return {i}\n" for i in range(30)))
    b = DocInput(source(2, sha="b"), shared + "\n" + "\n".join(f"def g{i}():\n    return {i}\n" for i in range(30)))
    result = compress_documents([a, b], ["commun"], 700)
    text = result.render()
    assert "corps identique à S1:commun" in text


def test_contenu_partiel_signale():
    doc = DocInput(source(1), "def a():\n    return 1\n", is_complete=False)
    block = compress_documents([doc], ["a"], 5000).blocks[0]
    assert "CONTENU PARTIEL" in block.text and "read_file" in block.text


def test_aucune_entree():
    assert compress_documents([], ["a"], 100).blocks == []


def test_rendu_sans_troncature_arbitraire_les_lignes_incluses_sont_listees():
    content = big_python()
    block = compress_documents([DocInput(source(1), content)], ["parse_json"], estimate_tokens(content) // 4).blocks[0]
    total = len(content.splitlines())
    assert block.total_lines == total and block.included_ranges
    assert all(1 <= a <= b <= total for a, b in block.included_ranges)
