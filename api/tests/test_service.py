"""Pipeline complet : recherche, compression, boucle d'outils, validation."""

import asyncio
import json

import pytest

from conftest import FakeEngine, FakeProvider, make_config, run, tool_call, turn
from icc_ai.conversation import InMemoryConversationRepository
from icc_ai.errors import EngineError, ProviderError, ToolsUnsupportedError
from icc_ai.service import SearchParams, SearchService, compute_budgets

ANSWER = json.dumps({
    "summary": "parse_json appelle json.loads.",
    "sections": [{"title": "Code", "body": "Extrait.", "code_blocks": [
        {"language": "python",
         "code": 'def parse_json(text):\n    """Analyse un texte JSON."""\n    return json.loads(text)',
         "source_ids": ["S1"]}]}],
    "used_sources": ["S1"],
})


def make_service(store, provider, engine=None, **config):
    engine = engine or FakeEngine()
    if config is not None:
        store.save("ollama", make_config(**config))
    repo = InMemoryConversationRepository()
    service = SearchService(store, engine, provider_factory=lambda runtime, transport=None: provider,
                            repository=repo)
    return service, engine, repo


def search(service, query="parse_json", **kw):
    return run(service.run(SearchParams(query, **kw)))


def test_flux_complet_sans_outil(store):
    provider = FakeProvider([turn(ANSWER)])
    service, engine, repo = make_service(store, provider)
    response = search(service)

    assert response.status == "ok" and response.answer.summary.startswith("parse_json")
    assert response.sources[0].id == "S1" and response.sources[0].cited
    assert response.answer.sections[0].code_blocks[0].verified is True
    assert response.meta.provider == "ollama" and response.meta.run_id
    assert repo.get_run(response.meta.run_id).response is response

    # Le modèle a reçu la question et le code compressé avec l'identifiant de source.
    user = provider.calls[0]["messages"][-1].content
    assert "Question : parse_json" in user and "=== SOURCE S1 | codes/json_tools.py" in user
    assert "return json.loads(text)" in user
    # La base entière n'est pas envoyée : seuls les résultats pertinents le sont.
    assert "files.rs" not in user


def test_boucle_d_outils_puis_reponse(store):
    provider = FakeProvider([
        turn("", [tool_call("search_code", {"query": "fs read_to_string", "language": "rust"})]),
        turn("", [tool_call("read_file", {"source_id": "S2", "start_line": 1, "end_line": 3}, "c2")]),
        turn(json.dumps({"summary": "Rust lit un fichier.", "used_sources": ["S2"]})),
    ])
    service, engine, _ = make_service(store, provider)
    response = search(service, "parse_json")

    assert response.meta.tool_calls == 2
    assert [t.name for t in response.tool_trace] == ["search_code", "read_file"]
    assert all(t.ok for t in response.tool_trace)
    cited = {s.id for s in response.sources if s.cited}
    assert cited == {"S2"}
    # Les résultats d'outil sont renvoyés au modèle au tour suivant.
    roles = [m.role for m in provider.calls[-1]["messages"]]
    assert roles[-4:] == ["assistant", "tool", "assistant", "tool"]


def test_limite_d_appels_d_outils_respectee(store):
    loop = [turn("", [tool_call("list_filters", {}, f"c{i}")]) for i in range(2)]
    provider = FakeProvider(loop + [turn(ANSWER)])
    service, _, _ = make_service(store, provider, advanced={"max_tool_calls": 2})
    response = search(service)
    assert response.meta.tool_calls == 2
    # Après 2 appels, le troisième tour interdit les outils.
    assert provider.calls[-1]["allow_tools"] is False and provider.calls[0]["allow_tools"] is True


def test_appels_en_trop_dans_un_meme_tour_sont_refuses(store):
    calls = [tool_call("list_filters", {}, f"c{i}") for i in range(4)]
    provider = FakeProvider([turn("", calls), turn(ANSWER)])
    service, _, _ = make_service(store, provider, advanced={"max_tool_calls": 2})
    response = search(service)
    assert response.meta.tool_calls == 2
    last_tools = [m for m in provider.calls[-1]["messages"] if m.role == "tool"]
    assert len(last_tools) == 4 and "limite d'appels" in last_tools[-1].content


def test_outils_desactives_si_zero_appel(store):
    provider = FakeProvider([turn(ANSWER)])
    service, _, _ = make_service(store, provider, advanced={"max_tool_calls": 0})
    response = search(service)
    assert provider.calls[0]["tools"] == [] and response.meta.tool_calls == 0


def test_modele_sans_outils_reessaie_sans(store):
    provider = FakeProvider([ToolsUnsupportedError(), turn(ANSWER)])
    service, _, _ = make_service(store, provider)
    response = search(service)
    assert response.status == "ok" and response.meta.tools_enabled is False
    assert provider.calls[1]["tools"] == []
    assert any("ne gère pas les outils" in w for w in response.warnings)


def test_outil_en_erreur_n_interrompt_pas(store):
    provider = FakeProvider([
        turn("", [tool_call("read_file", {"source_id": "S42"})]), turn(ANSWER)])
    service, _, _ = make_service(store, provider)
    response = search(service)
    assert response.status == "ok" and response.tool_trace[0].ok is False
    assert "inconnue" in provider.calls[1]["messages"][-1].content


def test_reponse_non_structuree_geree(store):
    provider = FakeProvider([turn("Je ne sais pas formater en JSON.")])
    service, _, _ = make_service(store, provider)
    response = search(service)
    assert response.status == "ok" and "Je ne sais pas" in response.answer.summary
    assert any("format structuré" in w for w in response.warnings)


def test_identifiants_inventes_par_le_modele_retires(store):
    provider = FakeProvider([turn(json.dumps({"summary": "x", "used_sources": ["S1", "S77"]}))])
    service, _, _ = make_service(store, provider)
    response = search(service)
    assert {s.id for s in response.sources} <= {"S1", "S2"}
    assert any("S77" in w for w in response.warnings)


def test_reponse_vide_du_modele(store):
    service, _, _ = make_service(store, FakeProvider([turn("   ")]))
    with pytest.raises(ProviderError) as info:
        search(service)
    assert info.value.kind == "invalid_response"


def test_erreur_du_fournisseur_remontee(store):
    service, _, _ = make_service(store, FakeProvider([ProviderError("rate_limit", "Trop vite", 429)]))
    with pytest.raises(ProviderError) as info:
        search(service)
    assert info.value.kind == "rate_limit"


def test_sans_fournisseur_actif_resultats_bruts(store):
    provider = FakeProvider([])
    service = SearchService(store, FakeEngine(), provider_factory=lambda *a: provider)
    response = search(service)
    assert response.status == "search_only" and response.answer is None
    assert response.sources and any("Aucun fournisseur" in w for w in response.warnings)
    assert provider.calls == []


def test_aucun_resultat_sans_outils(store):
    provider = FakeProvider([])
    service, _, _ = make_service(store, provider, advanced={"max_tool_calls": 0})
    response = search(service, "introuvable_zzz")
    assert response.status == "no_results" and provider.calls == []


def test_aucun_resultat_avec_outils_laisse_le_modele_chercher(store):
    provider = FakeProvider([
        turn("", [tool_call("search_code", {"query": "json"})]),
        turn(json.dumps({"summary": "Trouvé", "used_sources": ["S1"]})),
    ])
    service, _, _ = make_service(store, provider)
    response = search(service, "introuvable_zzz")
    assert response.status == "ok" and response.sources[0].cited
    assert "n'a trouvé aucune source" in provider.calls[0]["messages"][-1].content


def test_fichier_illisible_utilise_les_extraits_et_avertit(store):
    engine = FakeEngine()
    engine.fail_documents.update(engine.docs)
    service, _, _ = make_service(store, FakeProvider([turn(ANSWER)]), engine=engine)
    response = search(service)
    assert response.status == "ok" and any("Lecture impossible" in w for w in response.warnings)


def test_erreur_du_moteur_remontee(store):
    class Broken(FakeEngine):
        async def search(self, *a):
            raise EngineError("internal", "PostgreSQL indisponible")

    service, _, _ = make_service(store, FakeProvider([]), engine=Broken())
    with pytest.raises(EngineError):
        search(service)


def test_historique_transmis_pour_une_future_conversation(store):
    provider = FakeProvider([turn(ANSWER)])
    service, _, _ = make_service(store, provider)
    run(service.run(SearchParams("parse_json"), history=[("user", "Bonjour"), ("assistant", "Salut"), ("system", "ignoré")]))
    roles = [m.role for m in provider.calls[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_delai_total_depasse(store, monkeypatch):
    class Slow:
        async def chat(self, *a, **k):
            await asyncio.sleep(5)

    service, _, _ = make_service(store, Slow(), advanced={"timeout_s": 5, "max_tool_calls": 0})
    monkeypatch.setattr("icc_ai.service.MAX_TOTAL_TIMEOUT_S", 0.05)
    with pytest.raises(ProviderError) as info:
        search(service)
    assert info.value.kind == "timeout"


def test_annulation_propagee(store):
    class Hang:
        async def chat(self, *a, **k):
            await asyncio.sleep(30)

    service, _, _ = make_service(store, Hang())

    async def scenario():
        task = asyncio.ensure_future(service.run(SearchParams("parse_json")))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())


def test_budget_adapte_a_la_fenetre_du_modele(store):
    store.save("ollama", make_config(advanced={"context_window": 4096, "max_output_tokens": 512}))
    small = compute_budgets(store.active_runtime(), True)
    store.save("claude", make_config(base_url="https://api.anthropic.com", model="c", api_key="k",
                                     advanced={"context_window": 1_000_000}))
    big = compute_budgets(store.resolve("claude"), True)
    assert small[0] < big[0] <= 24_000
    assert small[1].max_result_chars <= big[1].max_result_chars <= 9000


def test_contexte_petit_bloque_les_outils_quand_plein(store):
    # Fenêtre minimale : les résultats d'outil remplissent le budget, la suite interdit les outils.
    class Wide(FakeEngine):
        async def list_filters(self):
            return {"languages": [{"slug": f"langage{i}", "documents": i} for i in range(40)],
                    "categories": []}

    provider = FakeProvider([
        turn("", [tool_call("list_filters", {})]), turn("", [tool_call("list_filters", {}, "c2")]), turn(ANSWER)])
    service, _, _ = make_service(store, provider, engine=Wide(),
                                 advanced={"context_window": 4200, "max_output_tokens": 64})
    response = search(service)
    assert response.status == "ok"
    assert any(not c["allow_tools"] for c in provider.calls)
