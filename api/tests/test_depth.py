# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Choix de la profondeur : aperçu, ciblé, approfondi (forcé ou déduit)."""

import pytest

from icc_ai.depth import GUIDANCE, assess

HISTORY = (("user", "comment faire une API en Python ?"), ("assistant", "Voici un exemple."))


def test_niveaux_et_plafonds():
    overview = assess("x", requested="overview")
    focused = assess("x", requested="focused")
    deep = assess("x", requested="deep")
    assert (overview.level, overview.search_limit, overview.max_attached, overview.tool_cap) == ("overview", 8, 3, 3)
    assert (focused.level, focused.search_limit, focused.max_attached, focused.tool_cap) == ("focused", None, 6, None)
    assert (deep.level, deep.search_limit, deep.max_attached, deep.tool_cap) == ("deep", None, 10, None)


def test_guidance_propre_a_chaque_niveau():
    assert set(GUIDANCE) == {"overview", "focused", "deep"}
    assert assess("x", requested="overview").guidance.startswith("MODE APERÇU")
    assert assess("x", requested="focused").guidance.startswith("MODE CIBLÉ")
    assert assess("x", requested="deep").guidance.startswith("MODE APPROFONDI")
    assert "follow_ups" in GUIDANCE["overview"] and "au plus 3 appels" in GUIDANCE["overview"]


def test_niveau_force_prime_sur_la_question():
    assert assess("approfondis en détail le chiffrement AES", requested="overview").level == "overview"
    assert assess("comment faire une API ?", requested="deep").level == "deep"
    assert assess("comment faire une API ?", HISTORY, requested="focused").level == "focused"


@pytest.mark.parametrize("requested", ["", "auto", "expert", "OVERVIEW", "nimporte quoi"])
def test_valeur_inconnue_ou_auto_deduit_le_niveau(requested):
    assert assess("comment faire une API en Python ?", requested=requested).level == "overview"


@pytest.mark.parametrize("query", [
    "comment faire une API en Python ?",
    "Comment créer un serveur web simple",
    "how do I read a file",
    "explique-moi les listes",
])
def test_question_generale_au_premier_tour_donne_apercu(query):
    assert assess(query).level == "overview"


@pytest.mark.parametrize("query", [
    "comment utiliser parse_json() ?",
    "où est défini read_all dans files.rs",
    "montre le fichier main.py",
    "comment chiffrer un fichier avec AES",
    "gestion de jwt et oauth",
    "comment faire du docker compose",
    "exemple d'une API asynchrone",
    "optimiser les performances",
    "comment utiliser `json.loads` ?",
    "fooBar dans le code",
])
def test_question_precise_donne_cible(query):
    assert assess(query).level == "focused"


def test_question_longue_sans_precision_donne_cible():
    query = "je voudrais savoir comment on peut faire pour construire une petite application " \
            "qui affiche des choses sur une page"
    assert len(query.split()) > 14 and assess(query).level == "focused"


def test_frontiere_de_14_mots():
    fourteen = "comment faire une petite application qui affiche une liste de choses sur la page"
    assert len(fourteen.split()) == 14 and assess(fourteen).level == "overview"
    assert assess(fourteen + " web").level == "focused"


@pytest.mark.parametrize("query", [
    "approfondis cette réponse",
    "Peux-tu détailler davantage ?",
    "explique en détail",
    "donne plus de détails",
    "va plus loin",
    "aller plus loin sur ce sujet",
    "je veux une réponse complète",
    "une analyse exhaustive",
    "creuse le sujet",
    "explique en profondeur",
    "level avancé",
    "ÉVALUATION POUSSÉE",
    "give me more details",
    "go deeper please",
    "deep dive into this",
    "in-depth explanation",
    "advanced usage",
    "can you elaborate",
])
def test_demande_d_approfondissement(query):
    assert assess(query).level == "deep"
    assert assess(query, HISTORY).level == "deep"


def test_mot_proche_n_est_pas_une_demande_d_approfondissement():
    assert assess("comment faire une API ?").level == "overview"
    assert assess("un exemple de poussin en python").level != "deep"


def test_suite_de_conversation_reste_ciblee():
    assert assess("et avec Flask ?", HISTORY).level == "focused"
    assert assess("comment faire une API en Python ?", HISTORY).level == "focused"
    assert assess("montre parse_json", HISTORY).level == "focused"


def test_longue_question_precise_en_suite_de_conversation_devient_approfondie():
    long_specific = ("pourrais-tu me montrer comment ajouter une authentification avec jwt à cette API "
                     "en gardant la même structure de fichiers et de fonctions que dans ton exemple précédent")
    assert len(long_specific.split()) > 18
    assert assess(long_specific, HISTORY).level == "deep"
    assert assess(long_specific).level == "focused"  # au premier tour : jamais approfondi d'office


def test_historique_vide_equivaut_a_premier_tour():
    assert assess("comment faire une API ?", ()).level == "overview"
    assert assess("comment faire une API ?", []).level == "overview"


def test_selection_ne_garde_que_le_pertinent():
    from icc_ai.depth import select_relevant
    hits = [{"document_id": "a", "score": 10.0}, {"document_id": "b", "score": 5.0},
            {"document_id": "c", "score": 3.0}, {"document_id": "d", "score": 0.5}]
    ids = lambda kept: [h["document_id"] for h in kept]  # noqa: E731
    kept, attach = select_relevant(hits, assess("x", requested="focused"))
    assert ids(kept) == ["a", "b", "c"] and attach == {"a", "b", "c"}  # d = 5 % < 15 % : écarté ; c = 30 % : joint
    kept, attach = select_relevant(hits, assess("x", requested="overview"))
    assert ids(kept) == ["a", "b", "c"] and attach == {"a", "b"}  # c = 30 % < 40 % : montré mais pas joint
    kept, attach = select_relevant(hits, assess("x", requested="deep"))
    assert ids(kept) == ["a", "b", "c"] and attach == {"a", "b", "c"}


def test_selection_cas_limites():
    from icc_ai.depth import select_relevant
    level = assess("x", requested="focused")
    assert select_relevant([], level) == ([], set())
    kept, attach = select_relevant([{"document_id": "z", "score": 0.0}, {"document_id": "y", "score": 0.0}], level)
    assert [h["document_id"] for h in kept] == ["z"] and attach == {"z"}
