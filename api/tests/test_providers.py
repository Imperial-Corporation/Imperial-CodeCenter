# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Connecteurs : traduction des messages/outils, erreurs, secrets (httpx mockés)."""

import json

import httpx
import pytest

from conftest import run
from icc_ai.catalog import get_spec
from icc_ai.config.models import AdvancedSettings
from icc_ai.config.store import ProviderRuntime
from icc_ai.errors import ProviderError, ToolsUnsupportedError
from icc_ai.providers.base import Message, ToolCall, ToolSpec
from icc_ai.providers.gemini import sanitize_schema
from icc_ai.providers.registry import create_provider

KEY = "sk-super-secret"
TOOL = ToolSpec("search_code", "Cherche", {
    "type": "object",
    "properties": {"query": {"type": "string", "title": "Q"}, "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
    "required": ["query"], "additionalProperties": False,
})


def runtime(provider_id, model="m", key=KEY, **advanced):
    spec = get_spec(provider_id)
    return ProviderRuntime(provider_id, spec.default_base_url, model, key,
                           AdvancedSettings.for_provider(provider_id, advanced))


def mock(handler):
    return httpx.MockTransport(handler)


def capture(response_json, status=200):
    seen = {}

    def handler(request: httpx.Request):
        seen["request"] = request
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=response_json)

    return seen, mock(handler)


HISTORY = [
    Message("system", "Sois bref."),
    Message("user", "Cherche json"),
    Message("assistant", "", [ToolCall("call_1", "search_code", {"query": "json"})]),
    Message("tool", "résultat", tool_call_id="call_1", name="search_code"),
]


# ---- OpenAI / Groq / OmniRoute ---------------------------------------------

def test_openai_requete_et_appel_d_outil():
    seen, transport = capture({
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "search_code", "arguments": "{\"query\": \"json\"}"}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    })
    provider = create_provider(runtime("openai", "gpt-4o-mini"), transport)
    out = run(provider.chat(HISTORY, [TOOL]))

    body, request = seen["body"], seen["request"]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert "max_completion_tokens" in body and "max_tokens" not in body
    assert body["tool_choice"] == "auto" and body["tools"][0]["function"]["name"] == "search_code"
    assistant = body["messages"][2]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"query": "json"}
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "résultat"}
    assert out.tool_calls[0].arguments == {"query": "json"} and out.usage["input_tokens"] == 12


def test_openai_modele_de_raisonnement_sans_temperature_et_tool_choice_none():
    seen, transport = capture({"choices": [{"message": {"content": "ok"}}]})
    run(create_provider(runtime("openai", "o3-mini"), transport).chat(HISTORY, [TOOL], allow_tools=False))
    assert "temperature" not in seen["body"] and seen["body"]["tool_choice"] == "none"


def test_omniroute_sans_cle_et_max_tokens():
    seen, transport = capture({"choices": [{"message": {"content": "ok"}}]})
    run(create_provider(runtime("omniroute", "x", key=None), transport).chat(HISTORY, []))
    assert "authorization" not in seen["request"].headers
    assert "max_tokens" in seen["body"] and "tools" not in seen["body"]


def test_openai_arguments_json_invalides():
    _, transport = capture({"choices": [{"message": {"tool_calls": [
        {"id": "t", "function": {"name": "search_code", "arguments": "{pas json"}}]}}]})
    out = run(create_provider(runtime("groq"), transport).chat(HISTORY, [TOOL]))
    assert out.tool_calls[0].arguments_error and out.tool_calls[0].arguments == {}


def test_openai_liste_des_modeles():
    _, transport = capture({"data": [{"id": "b"}, {"id": "a"}]})
    assert run(create_provider(runtime("openai"), transport).list_models()) == ["a", "b"]


# ---- Claude -------------------------------------------------------------------

def test_claude_systeme_separe_et_tool_result_regroupes():
    seen, transport = capture({
        "content": [{"type": "text", "text": "Bonjour"},
                    {"type": "tool_use", "id": "tu1", "name": "search_code", "input": {"query": "x"}}],
        "usage": {"input_tokens": 5, "output_tokens": 2}, "stop_reason": "tool_use",
    })
    history = HISTORY + [
        Message("assistant", "", [ToolCall("a", "search_code", {"query": "1"}), ToolCall("b", "search_code", {"query": "2"})]),
        Message("tool", "r1", tool_call_id="a", name="search_code"),
        Message("tool", "r2", tool_call_id="b", name="search_code"),
    ]
    out = run(create_provider(runtime("claude", "claude-sonnet-5-5"), transport).chat(history, [TOOL]))

    body, request = seen["body"], seen["request"]
    assert body["system"] == "Sois bref." and all(m["role"] != "system" for m in body["messages"])
    assert request.headers["x-api-key"] == KEY and request.headers["anthropic-version"]
    assert body["max_tokens"] > 0
    last = body["messages"][-1]
    assert last["role"] == "user" and [b["tool_use_id"] for b in last["content"]] == ["a", "b"]
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert out.text == "Bonjour" and out.tool_calls[0].id == "tu1"


def test_claude_tool_choice_none():
    seen, transport = capture({"content": [{"type": "text", "text": "fin"}]})
    run(create_provider(runtime("claude"), transport).chat(HISTORY, [TOOL], allow_tools=False))
    assert seen["body"]["tool_choice"] == {"type": "none"}


# ---- Gemini ---------------------------------------------------------------------

def test_gemini_schema_nettoye():
    cleaned = sanitize_schema(TOOL.parameters)
    assert "additionalProperties" not in cleaned
    assert cleaned["properties"]["limit"] == {"type": "integer", "nullable": True}
    assert "title" not in cleaned["properties"]["query"]


def test_gemini_cle_en_entete_et_roles():
    seen, transport = capture({
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "search_code", "args": {"query": "z"}}}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 1},
    })
    out = run(create_provider(runtime("gemini", "gemini-2.5-flash"), transport).chat(HISTORY, [TOOL]))
    request, body = seen["request"], seen["body"]
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
    assert str(request.url).endswith("models/gemini-2.5-flash:generateContent")
    assert body["systemInstruction"]["parts"][0]["text"] == "Sois bref."
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "search_code"
    assert out.tool_calls[0].arguments == {"query": "z"} and out.provider_data


def test_gemini_reponse_bloquee():
    _, transport = capture({"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("gemini"), transport).chat(HISTORY, []))
    assert info.value.kind == "invalid_response" and "SAFETY" in info.value.message


# ---- Ollama ---------------------------------------------------------------------

def test_ollama_arguments_objets_et_num_ctx():
    seen, transport = capture({
        "message": {"content": "", "tool_calls": [{"function": {"name": "search_code", "arguments": {"query": "q"}}}]},
        "prompt_eval_count": 7, "eval_count": 2,
    })
    out = run(create_provider(runtime("ollama", "llama3.1", key=None), transport).chat(HISTORY, [TOOL]))
    body = seen["body"]
    assert body["stream"] is False and body["options"]["num_ctx"] == 8192
    assert body["messages"][3]["role"] == "tool" and body["messages"][3]["tool_name"] == "search_code"
    assert out.tool_calls[0].arguments == {"query": "q"} and out.usage["output_tokens"] == 2


def test_ollama_modele_sans_outils():
    _, transport = capture({"error": "registry.ollama.ai/library/x does not support tools"}, status=400)
    with pytest.raises(ToolsUnsupportedError):
        run(create_provider(runtime("ollama", "x", key=None), transport).chat(HISTORY, [TOOL]))


def test_ollama_sans_outils_n_envoie_pas_la_liste():
    seen, transport = capture({"message": {"content": "ok"}})
    run(create_provider(runtime("ollama", key=None), transport).chat(HISTORY, [TOOL], allow_tools=False))
    assert "tools" not in seen["body"]


# ---- Erreurs transverses ----------------------------------------------------------

@pytest.mark.parametrize("status,kind", [
    (401, "auth"), (403, "auth"), (404, "not_found"), (429, "rate_limit"),
    (500, "server"), (504, "timeout"), (400, "bad_request"),
])
def test_erreurs_http_typees(status, kind):
    _, transport = capture({"error": {"message": f"boom {KEY}"}}, status=status)
    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("openai"), transport).chat(HISTORY, []))
    assert info.value.kind == kind
    assert KEY not in info.value.message  # la clé n'apparaît jamais


def test_delai_depasse_et_reseau():
    def slow(request):
        raise httpx.ReadTimeout("lent", request=request)

    def down(request):
        raise httpx.ConnectError(f"refusé {KEY}", request=request)

    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("openai"), mock(slow)).chat(HISTORY, []))
    assert info.value.kind == "timeout"

    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("openai"), mock(down)).chat(HISTORY, []))
    assert info.value.kind == "network" and KEY not in info.value.message


def test_reponse_non_json():
    transport = mock(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("openai"), transport).chat(HISTORY, []))
    assert info.value.kind == "invalid_response"


def test_test_connexion_modele_absent_de_la_liste():
    _, transport = capture({"data": [{"id": "autre"}]})
    report = run(create_provider(runtime("openai", "gpt-x"), transport).test_connection())
    assert report.ok and "n'apparaît pas" in report.message and report.models == ["autre"]


def test_test_connexion_reussie():
    _, transport = capture({"data": [{"id": "gpt-x"}]})
    report = run(create_provider(runtime("openai", "gpt-x"), transport).test_connection())
    assert report.ok and report.message == "Connexion réussie."


# ---- Passerelles qui répondent en SSE ou en HTML (cas OmniRoute) -----------------

SSE = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"Bon"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"jour"}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","function":{"name":"search_code","arguments":"{\\"que"}}]}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ry\\":\\"x\\"}"}}]},"finish_reason":"tool_calls"}],'
    '"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


def test_omniroute_flux_sse_recompose():
    transport = mock(lambda r: httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream"}))
    out = run(create_provider(runtime("omniroute", "combo-ultime", key=None), transport).chat(HISTORY, [TOOL]))
    assert out.text == "Bonjour" and out.finish_reason == "tool_calls"
    assert out.tool_calls[0].id == "t1" and out.tool_calls[0].arguments == {"query": "x"}
    assert out.usage == {"input_tokens": 4, "output_tokens": 2}


def test_requete_openai_demande_une_reponse_unique():
    seen, transport = capture({"choices": [{"message": {"content": "ok"}}]})
    run(create_provider(runtime("omniroute", "x", key=None), transport).chat(HISTORY, []))
    assert seen["body"]["stream"] is False


def test_page_html_signale_une_mauvaise_url():
    transport = mock(lambda r: httpx.Response(200, text="<!doctype html><html><body>Dashboard</body></html>",
                                              headers={"content-type": "text/html"}))
    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("omniroute", "x", key=None), transport).chat(HISTORY, []))
    assert info.value.kind == "invalid_response"
    assert "text/html" in info.value.message and "URL de l'API" in info.value.message


def test_texte_brut_montre_le_debut_sans_la_cle():
    transport = mock(lambda r: httpx.Response(200, text=f"erreur interne {KEY}", headers={"content-type": "text/plain"}))
    with pytest.raises(ProviderError) as info:
        run(create_provider(runtime("openai"), transport).chat(HISTORY, []))
    assert "erreur interne" in info.value.message and KEY not in info.value.message


def test_openai_compatible_sans_outils_leve_tools_unsupported():
    _, transport = capture({"error": {"message": "This model does not support tools"}}, status=400)
    with pytest.raises(ToolsUnsupportedError):
        run(create_provider(runtime("omniroute", "x", key=None), transport).chat(HISTORY, [TOOL]))


@pytest.mark.parametrize("provider_id", ["openrouter", "mistral", "deepseek", "xai", "together", "fireworks",
                                         "cerebras", "nvidia", "cohere", "lmstudio", "llamacpp", "custom"])
def test_nouveaux_fournisseurs_compatibles_openai(provider_id):
    """Chaque fournisseur ajouté parle le protocole OpenAI vers sa propre URL, avec la clé en Bearer."""
    from icc_ai.catalog import PROVIDERS

    spec = PROVIDERS[provider_id]
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    base = spec.default_base_url if provider_id != "custom" else "https://llm.example.org/v1"
    rt = runtime(provider_id, "un-modele")
    rt = type(rt)(provider_id, base, "un-modele", "cle-secrete" if spec.needs_api_key else None, rt.advanced)
    out = run(create_provider(rt, httpx.MockTransport(handler)).chat(HISTORY, []))
    assert out.text == "ok"
    assert seen["url"].startswith(base.rstrip("/")) and seen["url"].endswith("/chat/completions")
    assert seen["auth"] == ("Bearer cle-secrete" if spec.needs_api_key else None)
