"""ChatService : conversations, registre de sources stable, historique, limites, profondeur."""

import asyncio
import json

import pytest

from conftest import FakeEngine, FakeProvider, PY_CODE, RUST_CODE, make_config, run, tool_call, turn
from icc_ai import chat as chat_module
from icc_ai.chat import ChatService, answer_to_text
from icc_ai.depth import GUIDANCE
from icc_ai.errors import ChatError, ProviderError
from icc_ai.response import (
    AnswerOut, CodeBlockOut, ModelAnswer, SectionOut, _clean_follow_ups, build_answer, parse_model_output,
)
from icc_ai.service import SearchParams, SearchService
from icc_ai.sources import SourceRegistry

PARSE = 'def parse_json(text):\n    """Analyse un texte JSON."""\n    return json.loads(text)'
JSON_ID = "11111111-1111-1111-1111-111111111111"
RUST_ID = "22222222-2222-2222-2222-222222222222"


def answer(summary="Réponse", used=("S1",), code=None, blocks_ids=("S1",), **extra):
    data = {"summary": summary, "used_sources": list(used), **extra}
    if code is not None:
        data["sections"] = [{"title": "Code", "body": "Extrait.", "code_blocks": [
            {"language": "python", "code": code, "source_ids": list(blocks_ids)}]}]
    return json.dumps(data)


@pytest.fixture
def build(store):
    def make(provider, engine=None, **chat_kwargs):
        store.save("ollama", make_config())
        engine = engine or FakeEngine()
        service = SearchService(store, engine, provider_factory=lambda runtime, transport=None: provider)
        return ChatService(service, **chat_kwargs), engine

    return make


def many_documents(count=10):
    return {
        f"{i:08d}-0000-0000-0000-000000000000": {
            "path": f"codes/doc{i}.py", "language": "python",
            "content": f'"""Outil {i}."""\nimport json\n\n\ndef outil_{i}(x):\n    return json.dumps(x)\n',
        } for i in range(count)
    }


def search(service, query="parse_json", depth="auto", **kw):
    return run(service.search(SearchParams(query, **kw), depth))


def reply(service, cid, message, **kw):
    return run(service.reply(cid, message, **kw))


# --------------------------------------------------------------------------
# Ouverture d'une conversation
# --------------------------------------------------------------------------

def test_conversation_creee_apres_une_recherche_reussie(build):
    chat, _ = build(FakeProvider([turn(answer())]))
    response = search(chat)
    assert response.status == "ok" and response.meta.conversation_id and len(chat) == 1
    session = chat.get(response.meta.conversation_id)
    assert session.turns == 1 and session.history[0] == ("user", "parse_json")
    assert session.history[1][0] == "assistant" and "Réponse" in session.history[1][1]
    assert len(session.registry) == 1


def test_pas_de_conversation_sans_fournisseur(store):
    engine = FakeEngine()
    chat = ChatService(SearchService(store, engine))
    response = search(chat)
    assert response.status == "search_only" and response.meta.conversation_id == "" and len(chat) == 0
    with pytest.raises(ChatError) as err:
        chat.get("inexistant")
    assert err.value.kind == "not_found"


def test_pas_de_conversation_sans_resultat(store):
    chat = ChatService(SearchService(store, FakeEngine()))
    response = search(chat, "zzzzqqqq")
    assert response.status == "no_results" and response.meta.conversation_id == "" and len(chat) == 0


def test_pas_de_conversation_si_le_fournisseur_echoue(build):
    chat, _ = build(FakeProvider([ProviderError("timeout", "Trop long")]))
    with pytest.raises(ProviderError):
        search(chat)
    assert len(chat) == 0


def test_identifiants_de_conversation_uniques(build):
    chat, _ = build(FakeProvider([turn(answer()), turn(answer())]))
    a, b = search(chat), search(chat)
    assert a.meta.conversation_id != b.meta.conversation_id and len(chat) == 2


# --------------------------------------------------------------------------
# Registre de sources stable, texte déjà vu
# --------------------------------------------------------------------------

def test_ids_de_sources_stables_d_un_tour_a_l_autre(build):
    provider = FakeProvider([turn(answer()), turn(answer(used=["S2"])), turn(answer(used=["S1", "S2"]))])
    chat, engine = build(provider)
    first = search(chat)
    cid = first.meta.conversation_id
    assert [(s.id, s.path) for s in first.sources] == [("S1", "codes/json_tools.py")]

    second = reply(chat, cid, "read_all")
    ids = {s.id: s.path for s in second.sources}
    assert ids == {"S1": "codes/json_tools.py", "S2": "codes/files.rs"}
    assert second.meta.conversation_id == cid

    third = reply(chat, cid, "parse_json encore")
    assert {s.id: s.path for s in third.sources} == ids  # pas de doublon, mêmes identifiants
    assert len(chat.get(cid).registry) == 2
    assert {s.id for s in third.sources if s.cited} == {"S1", "S2"}


def test_texte_deja_vu_reste_valable_pour_verified(build):
    provider = FakeProvider([turn(answer(code=PARSE)), turn(answer(code=PARSE))])
    chat, engine = build(provider)
    first = search(chat)
    assert first.answer.sections[0].code_blocks[0].verified is True
    cid = first.meta.conversation_id
    reads_before = [c for c in engine.calls if c[0] == "get_document"]

    second = reply(chat, cid, "montre-moi encore parse_json")
    assert second.answer.sections[0].code_blocks[0].verified is True
    # S1 n'est pas relu ni rejoint : il l'a été au premier tour.
    assert [c for c in engine.calls if c[0] == "get_document"] == reads_before
    user = provider.calls[1]["messages"][-1].content
    assert "Autres sources connues" in user and "S1 | codes/json_tools.py" in user
    assert "=== SOURCE S1" not in user


def test_code_invente_n_est_pas_verifie_meme_en_conversation(build):
    provider = FakeProvider([turn(answer()), turn(answer(code="def inventee():\n    return 0"))])
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    block = reply(chat, cid, "parse_json").answer.sections[0].code_blocks[0]
    assert block.verified is False


def test_source_inconnue_signalee_en_conversation(build):
    provider = FakeProvider([turn(answer()), turn(answer(used=["S1", "S9"]))])
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    second = reply(chat, cid, "parse_json")
    assert any("S9" in w for w in second.warnings) and {s.id for s in second.sources} == {"S1"}


# --------------------------------------------------------------------------
# Historique transmis au fournisseur
# --------------------------------------------------------------------------

def test_historique_transmis_au_fournisseur(build):
    provider = FakeProvider([turn(answer("Première synthèse", code=PARSE)), turn(answer("Seconde"))])
    chat, _ = build(provider)
    cid = search(chat, "parse_json").meta.conversation_id
    reply(chat, cid, "et pour les erreurs ?")
    messages = provider.calls[1]["messages"]
    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[1].content == "parse_json"
    assert "Première synthèse" in messages[2].content and "```python" in messages[2].content
    assert PARSE in messages[2].content
    assert "Question : et pour les erreurs ?" in messages[3].content
    # Le premier tour n'avait pas d'historique.
    assert [m.role for m in provider.calls[0]["messages"]] == ["system", "user"]


def test_historique_borne_a_huit_messages_et_session_a_deux_fois_max_tours(build):
    script = [turn(answer(f"R{i}")) for i in range(7)]
    provider = FakeProvider(script)
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    for i in range(6):
        reply(chat, cid, f"parse_json {i}")
    history_sent = [m for m in provider.calls[6]["messages"] if m.role in ("user", "assistant")]
    assert len(history_sent) <= 8 + 1  # 8 messages d'historique + la question courante
    assert len(chat.get(cid).history) == 14


def test_historique_de_session_borne(build, monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_TURNS", 3)
    provider = FakeProvider([turn(answer()) for _ in range(3)])
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    reply(chat, cid, "parse_json 1")
    reply(chat, cid, "parse_json 2")
    session = chat.get(cid)
    assert session.turns == 3 and len(session.history) <= 6


def test_answer_to_text_borne_et_complet():
    long_answer = AnswerOut(
        summary="S" * 5000,
        sections=[SectionOut(title="Titre", body="corps", code_blocks=[
            CodeBlockOut(language="python", code="x = 1", source_ids=[], verified=False)])])
    text = answer_to_text(long_answer)
    assert len(text) == chat_module.HISTORY_TEXT_CHARS
    short = answer_to_text(AnswerOut(
        summary="Synthèse", sections=[SectionOut(title="Titre", body="corps", code_blocks=[
            CodeBlockOut(language="python", code="x = 1", source_ids=[], verified=False)])]))
    assert short == "Synthèse\n\n### Titre\n\ncorps\n\n```python\nx = 1\n```"


# --------------------------------------------------------------------------
# Limites et erreurs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("message,kind", [("", "invalid"), ("   \n\t", "invalid"), ("x" * 2001, "invalid")])
def test_message_invalide(build, message, kind):
    chat, _ = build(FakeProvider([turn(answer())]))
    cid = search(chat).meta.conversation_id
    with pytest.raises(ChatError) as err:
        reply(chat, cid, message)
    assert err.value.kind == kind
    assert chat.get(cid).turns == 1


def test_message_a_la_limite_accepte(build):
    chat, _ = build(FakeProvider([turn(answer()), turn(answer())]))
    cid = search(chat).meta.conversation_id
    assert reply(chat, cid, "p" * 2000).status == "ok"


def test_conversation_introuvable(build):
    chat, _ = build(FakeProvider([]))
    with pytest.raises(ChatError) as err:
        reply(chat, "n-existe-pas", "bonjour")
    assert err.value.kind == "not_found" and "introuvable" in err.value.message


def test_limite_de_tours(build, monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_TURNS", 3)
    chat, _ = build(FakeProvider([turn(answer()) for _ in range(3)]))
    cid = search(chat).meta.conversation_id
    reply(chat, cid, "parse_json 1")
    reply(chat, cid, "parse_json 2")
    with pytest.raises(ChatError) as err:
        reply(chat, cid, "parse_json 3")
    assert err.value.kind == "too_long"


def test_limite_de_sources(build, monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_SOURCES", 1)
    chat, _ = build(FakeProvider([turn(answer())]))
    cid = search(chat).meta.conversation_id
    with pytest.raises(ChatError) as err:
        reply(chat, cid, "parse_json")
    assert err.value.kind == "too_long"


def test_session_expiree(build):
    chat, _ = build(FakeProvider([turn(answer())]), ttl=60)
    cid = search(chat).meta.conversation_id
    chat.get(cid).touched -= 120  # inactive depuis deux minutes
    with pytest.raises(ChatError) as err:
        reply(chat, cid, "parse_json")
    assert err.value.kind == "not_found" and len(chat) == 0


def test_activite_prolonge_la_session(build):
    chat, _ = build(FakeProvider([turn(answer()), turn(answer())]), ttl=60)
    cid = search(chat).meta.conversation_id
    chat.get(cid).touched -= 50
    reply(chat, cid, "parse_json")  # remet le compteur à zéro
    chat.get(cid).touched -= 50
    assert chat.get(cid).id == cid


def test_nombre_de_sessions_borne(build):
    chat, _ = build(FakeProvider([turn(answer()) for _ in range(4)]), max_sessions=2)
    ids = [search(chat).meta.conversation_id for _ in range(3)]
    assert len(chat) == 2
    with pytest.raises(ChatError):
        chat.get(ids[0])  # la plus ancienne est évincée
    assert chat.get(ids[1]) and chat.get(ids[2])
    chat.get(ids[1])  # consultée : devient la plus récente
    search(chat)
    assert chat.get(ids[1]) and len(chat) == 2
    with pytest.raises(ChatError):
        chat.get(ids[2])


class Blocking(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def chat(self, messages, tools, allow_tools=True):
        self.started.set()
        await self.gate.wait()
        return await super().chat(messages, tools, allow_tools)


def test_conversation_occupee(build):
    provider = Blocking([turn(answer()), turn(answer())])
    chat, _ = build(provider)
    provider.gate.set()
    cid = search(chat).meta.conversation_id
    provider.gate.clear()
    provider.started.clear()

    async def scenario():
        first = asyncio.ensure_future(chat.reply(cid, "parse_json 1"))
        await provider.started.wait()
        with pytest.raises(ChatError) as err:
            await chat.reply(cid, "parse_json 2")
        assert err.value.kind == "busy"
        # Une session occupée n'est jamais purgée, même expirée.
        chat.get(cid).touched -= 10**6
        chat._purge()
        assert len(chat) == 1
        provider.gate.set()
        return await first

    assert run(scenario()).status == "ok"
    assert chat.get(cid).turns == 2


def test_echec_d_un_tour_ne_modifie_pas_la_conversation(build):
    provider = FakeProvider([turn(answer()), ProviderError("rate_limit", "Limite"), turn(answer("Reprise"))])
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    before = list(chat.get(cid).history)
    with pytest.raises(ProviderError):
        reply(chat, cid, "parse_json")
    session = chat.get(cid)
    assert session.history == before and session.turns == 1 and not session.lock.locked()
    assert reply(chat, cid, "parse_json").answer.summary == "Reprise" and session.turns == 2


def test_tour_sans_reponse_ia_n_alimente_pas_l_historique(build, monkeypatch):
    chat, _ = build(FakeProvider([turn(answer())]))
    cid = search(chat).meta.conversation_id
    monkeypatch.setattr(chat._search._store, "active_runtime", lambda: None)
    response = reply(chat, cid, "parse_json")
    assert response.status == "search_only" and response.answer is None
    assert chat.get(cid).turns == 1 and len(chat.get(cid).history) == 2


def test_filtres_langage_et_categorie_transmis(build):
    chat, engine = build(FakeProvider([turn(answer()), turn(answer())]))
    cid = search(chat).meta.conversation_id
    reply(chat, cid, "read_all", language="rust", category="fichiers")
    assert search_calls(engine)[1][3:] == ("rust", "fichiers")


# --------------------------------------------------------------------------
# Les outils restent disponibles au tour suivant
# --------------------------------------------------------------------------

def test_outils_disponibles_au_tour_suivant(build):
    provider = FakeProvider([
        turn(answer()),
        turn("", [tool_call("search_code", {"query": "read_all", "language": "rust"})]),
        turn(answer("Trouvé via l'outil", used=["S2"])),
        turn(answer("Encore", used=["S1", "S2"])),
    ])
    chat, engine = build(provider)
    cid = search(chat).meta.conversation_id

    second = reply(chat, cid, "y a-t-il du Rust ?")
    assert second.meta.tool_calls == 1 and second.tool_trace[0].name == "search_code" and second.tool_trace[0].ok
    assert provider.calls[1]["tools"] and provider.calls[1]["allow_tools"] is True
    assert {s.id: s.path for s in second.sources}["S2"] == "codes/files.rs"
    assert [s.id for s in second.sources if s.cited] == ["S2"]

    third = reply(chat, cid, "résume les deux")
    assert provider.calls[3]["tools"]  # toujours disponibles
    assert {s.id for s in third.sources} == {"S1", "S2"} and len(chat.get(cid).registry) == 2


def test_lecture_de_fichier_par_outil_en_conversation(build):
    provider = FakeProvider([
        turn(answer()),
        turn("", [tool_call("read_file", {"source_id": "S1", "start_line": 1, "end_line": 4})]),
        turn(answer(code="def parse_json(text):", used=["S1"])),
    ])
    chat, _ = build(provider)
    cid = search(chat).meta.conversation_id
    second = reply(chat, cid, "lis le début du fichier")
    assert second.meta.tool_calls == 1 and second.tool_trace[0].name == "read_file"
    assert second.answer.sections[0].code_blocks[0].verified is True


# --------------------------------------------------------------------------
# Profondeur
# --------------------------------------------------------------------------

def search_calls(engine):
    return [c for c in engine.calls if c[0] == "search"]


def read_calls(engine):
    return [c for c in engine.calls if c[0] == "get_document"]


def test_overview_limite_recherche_fichiers_et_outils(build):
    three_tools = turn("", [tool_call("search_code", {"query": "json"}, f"c{i}") for i in range(3)])
    provider = FakeProvider([three_tools, turn(answer(used=[]))])
    chat, engine = build(provider, engine=FakeEngine(many_documents()))
    response = search(chat, "comment lire un fichier json", limit=10)
    assert response.meta.depth == "overview"
    assert search_calls(engine)[0][2] == 6  # plafond de recherche
    assert len(read_calls(engine)) == 3  # fichiers joints d'emblée
    assert response.meta.tool_calls == 2  # plafond d'outils
    tool_messages = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert len(tool_messages) == 3 and "limite d'appels d'outils atteinte" in tool_messages[2].content
    assert provider.calls[0]["messages"][0].content.endswith(GUIDANCE["overview"])


def test_deep_ne_limite_pas(build):
    three_tools = turn("", [tool_call("search_code", {"query": "json"}, f"c{i}") for i in range(3)])
    provider = FakeProvider([three_tools, turn(answer(used=[]))])
    chat, engine = build(provider, engine=FakeEngine(many_documents()))
    response = search(chat, "comment lire un fichier json", depth="deep", limit=10)
    assert response.meta.depth == "deep"
    assert search_calls(engine)[0][2] == 10
    assert len(read_calls(engine)) == 8
    assert response.meta.tool_calls == 3
    assert provider.calls[0]["messages"][0].content.endswith(GUIDANCE["deep"])


def test_focused_plafond_de_fichiers_joints(build):
    chat, engine = build(FakeProvider([turn(answer(used=[]))]), engine=FakeEngine(many_documents()))
    response = search(chat, "json dumps() utiliser", limit=10)
    assert response.meta.depth == "focused" and len(read_calls(engine)) == 6
    assert search_calls(engine)[0][2] == 10


@pytest.mark.parametrize("depth", ["overview", "focused", "deep"])
def test_le_prompt_systeme_contient_la_consigne_du_niveau(build, depth):
    provider = FakeProvider([turn(answer())])
    chat, _ = build(provider)
    response = search(chat, depth=depth)
    system = provider.calls[0]["messages"][0]
    assert system.role == "system" and system.content.endswith(GUIDANCE[depth])
    assert response.meta.depth == depth
    for other in {"overview", "focused", "deep"} - {depth}:
        assert GUIDANCE[other] not in system.content


def test_profondeur_en_conversation(build):
    provider = FakeProvider([turn(answer()), turn(answer()), turn(answer()), turn(answer())])
    chat, engine = build(provider, engine=FakeEngine(many_documents()))
    first = search(chat, "comment lire un fichier json")
    assert first.meta.depth == "overview"
    cid = first.meta.conversation_id
    assert reply(chat, cid, "et avec json ?").meta.depth == "focused"  # suite : ciblé
    assert reply(chat, cid, "approfondis le sujet").meta.depth == "deep"
    forced = reply(chat, cid, "json", depth="overview")
    assert forced.meta.depth == "overview"
    assert provider.calls[3]["messages"][0].content.endswith(GUIDANCE["overview"])
    # Les tours de conversation cherchent 5 résultats ; le plafond d'aperçu (6) ne s'y applique pas en trop.
    assert [c[2] for c in search_calls(engine)[1:]] == [5, 5, 5]


def test_overview_en_conversation_limite_les_outils(build):
    provider = FakeProvider([
        turn(answer()),
        turn("", [tool_call("search_code", {"query": "json"}, f"c{i}") for i in range(3)]),
        turn(answer()),
    ])
    chat, _ = build(provider, engine=FakeEngine(many_documents()))
    cid = search(chat, "comment lire un fichier json").meta.conversation_id
    second = reply(chat, cid, "json", depth="overview")
    assert second.meta.tool_calls == 2


# --------------------------------------------------------------------------
# follow_ups
# --------------------------------------------------------------------------

def test_follow_ups_nettoyes():
    raw = ["  Comment gérer\nles   erreurs ?  ", "comment gérer les erreurs ?", "", "   ", "x" * 300,
           "Question 4", "Question 5", "Question 6", "<script>alert(1)</script>"]
    cleaned = _clean_follow_ups(raw)
    assert cleaned[0] == "Comment gérer les erreurs ?"  # espaces normalisés, doublon (casse) retiré
    assert len(cleaned) == 4 and len(set(c.lower() for c in cleaned)) == 4
    assert cleaned[1] == "x" * 140 and "" not in cleaned
    assert _clean_follow_ups([]) == [] and _clean_follow_ups(["a"]) == ["a"]


def test_follow_ups_dans_la_reponse_complete(build):
    data = answer(follow_ups=["A ?", "a ?", "B ?", "C ?", "D ?", "E ?"])
    chat, _ = build(FakeProvider([turn(data)]))
    response = search(chat)
    assert response.answer.follow_ups == ["A ?", "B ?", "C ?", "D ?"]
    assert response.meta.conversation_id


def test_follow_ups_trop_nombreux_ou_mal_formes_ne_perdent_pas_la_reponse():
    registry = SourceRegistry()
    many = json.dumps({"summary": "Texte", "follow_ups": [f"Piste {i} ?" for i in range(30)]})
    parsed, warnings = parse_model_output(many)
    assert not warnings and parsed.summary == "Texte"
    mixed = json.dumps({"summary": "Texte", "follow_ups": ["Ok ?", 3, None, {"a": 1}, ["x"], "Autre ?"]})
    parsed, warnings = parse_model_output(mixed)
    assert not warnings
    out, _, _ = build_answer(parsed, registry)
    assert out.follow_ups == ["Ok ?", "Autre ?"]
    parsed, warnings = parse_model_output(json.dumps({"summary": "T", "follow_ups": "Une seule ?"}))
    assert not warnings and build_answer(parsed, registry)[0].follow_ups == ["Une seule ?"]
    parsed, warnings = parse_model_output(json.dumps({"summary": "T", "follow_ups": {"a": 1}}))
    assert not warnings and parsed.follow_ups == []


def test_follow_ups_absents_par_defaut():
    parsed, _ = parse_model_output(json.dumps({"summary": "T"}))
    out, _, _ = build_answer(parsed, SourceRegistry())
    assert out.follow_ups == []
