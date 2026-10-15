"""Fixtures communes : faux moteur Rust et faux fournisseur scripté."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from icc_ai.config.crypto import SecretBox  # noqa: E402
from icc_ai.config.models import ProviderConfigIn  # noqa: E402
from icc_ai.config.store import ConfigStore  # noqa: E402
from icc_ai.errors import EngineError  # noqa: E402
from icc_ai.providers.base import AssistantTurn, ToolCall  # noqa: E402

from cryptography.fernet import Fernet  # noqa: E402


def run(coro):
    """Exécute une coroutine dans un test synchrone."""
    return asyncio.run(coro)


PY_CODE = '''"""Outils JSON."""
import json


def parse_json(text):
    """Analyse un texte JSON."""
    return json.loads(text)


def unrelated():
    return 42
'''

RUST_CODE = """use std::fs;

pub fn read_all(path: &str) -> String {
    fs::read_to_string(path).unwrap_or_default()
}
"""


class FakeEngine:
    """Implémente EngineGateway sans Rust ni PostgreSQL."""

    def __init__(self, docs: dict[str, dict] | None = None):
        self.docs = docs if docs is not None else {
            "11111111-1111-1111-1111-111111111111": {
                "path": "codes/json_tools.py", "language": "python", "content": PY_CODE,
            },
            "22222222-2222-2222-2222-222222222222": {
                "path": "codes/files.rs", "language": "rust", "content": RUST_CODE,
            },
        }
        self.calls: list[tuple] = []
        self.fail_documents: set[str] = set()

    async def search(self, query, limit, language, category):
        self.calls.append(("search", query, limit, language, category))
        terms = [t for t in query.lower().split() if len(t) > 1]
        results = []
        for doc_id, doc in self.docs.items():
            if language and doc["language"] != language:
                continue
            text = doc["content"].lower()
            score = sum(text.count(t) for t in terms)
            if score == 0:
                continue
            first = next(i for i, l in enumerate(doc["content"].splitlines(), 1)
                         if any(t in l.lower() for t in terms))
            results.append({
                "document_id": doc_id, "title": doc["path"].split("/")[-1],
                "relative_path": doc["path"], "language": doc["language"],
                "language_name": doc["language"].title(), "categories": [],
                "score": float(score), "file_size": len(doc["content"]),
                "content_sha256": f"sha{doc_id[:4]}", "summary": None,
                "line_start": first, "line_end": first + 2,
                "snippet": "\n".join(doc["content"].splitlines()[first - 1:first + 2]),
                "content_matched": True,
            })
        results.sort(key=lambda r: -r["score"])
        return {"terms": terms, "results": results[:limit], "truncated": False,
                "candidates": len(self.docs), "scanned_files": len(self.docs), "took_ms": 1}

    async def get_document(self, document_id, start_line=None, end_line=None, max_bytes=None):
        self.calls.append(("get_document", document_id, start_line, end_line, max_bytes))
        if document_id in self.fail_documents:
            raise EngineError("forbidden", "Chemin en dehors des dossiers autorisés.")
        doc = self.docs.get(document_id)
        if doc is None:
            raise EngineError("not_found", "Document introuvable.")
        lines = doc["content"].splitlines()
        first = start_line or 1
        last = min(end_line or len(lines), len(lines))
        chosen = lines[first - 1:last]
        text = "\n".join(chosen) + ("\n" if chosen else "")
        return {
            "document_id": document_id, "title": doc["path"], "relative_path": doc["path"],
            "language": doc["language"], "language_name": doc["language"].title(),
            "categories": [], "file_size": len(doc["content"]),
            "content_sha256": f"sha{document_id[:4]}", "content_matches_index": True,
            "summary": "Résumé de test", "symbols": [{"name": "parse_json"}],
            "dependencies": ["json"], "line_start": first, "line_end": first + len(chosen) - 1,
            "total_lines": len(lines), "truncated": False, "content": text,
        }

    async def list_filters(self):
        return {"languages": [{"slug": "python", "name": "Python", "documents": 1}],
                "categories": [{"slug": "json", "name": "JSON", "path": "JSON", "documents": 1}]}


class FakeProvider:
    """Fournisseur scripté : chaque appel à `chat` consomme une entrée."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, messages, tools, allow_tools=True):
        self.calls.append({"messages": list(messages), "tools": list(tools), "allow_tools": allow_tools})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def turn(text="", calls=()):
    return AssistantTurn(text=text, tool_calls=list(calls),
                         usage={"input_tokens": 10, "output_tokens": 5})


def tool_call(name, arguments, call_id="c1"):
    return ToolCall(id=call_id, name=name, arguments=arguments)


@pytest.fixture
def secret_box():
    return SecretBox(Fernet.generate_key())


@pytest.fixture
def store(tmp_path, secret_box):
    return ConfigStore(tmp_path / "data", secret_box)


def make_config(**overrides) -> ProviderConfigIn:
    values = {"base_url": "http://127.0.0.1:11434", "model": "llama3.1", "activate": True}
    values.update(overrides)
    return ProviderConfigIn(**values)
