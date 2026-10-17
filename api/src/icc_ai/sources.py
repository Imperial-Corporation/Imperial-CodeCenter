# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Registre des sources d'une recherche.

Les identifiants `S1`, `S2`… sont attribués par le serveur à partir des
résultats réellement récupérés. Le modèle ne manipule que ces identifiants :
il ne peut donc ni inventer un document, ni fabriquer un lien interne
présenté comme vérifié.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SOURCE_ID_RE = re.compile(r"^S\d{1,3}$")
_LINE_PREFIX_RE = re.compile(r"^\s*\d+\| ?", re.MULTILINE)

MAX_SEEN_CHARS = 200_000


@dataclass
class Excerpt:
    line_start: int
    line_end: int
    text: str


@dataclass
class Source:
    id: str
    document_id: str
    title: str
    path: str
    language: str
    language_name: str
    categories: list[str]
    sha256: str
    score: float
    summary: str | None = None
    excerpts: list[Excerpt] = field(default_factory=list)
    # Texte réellement montré au modèle (sans numéros de ligne), pour
    # vérifier les citations littérales.
    seen_text: str = ""
    # Vrai une fois le contenu joint au contexte d'un tour de conversation.
    attached: bool = False

    def add_seen(self, text: str) -> None:
        cleaned = _LINE_PREFIX_RE.sub("", text)
        if len(self.seen_text) < MAX_SEEN_CHARS:
            self.seen_text += "\n" + cleaned


def normalize_space(text: str) -> str:
    return " ".join(text.split())


class SourceRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, Source] = {}
        self._by_document: dict[str, Source] = {}

    def register_hit(self, hit: dict) -> Source:
        """Enregistre (ou retrouve) un document issu d'une recherche."""
        document_id = str(hit["document_id"])
        existing = self._by_document.get(document_id)

        if existing is None:
            existing = Source(
                id=f"S{len(self._by_id) + 1}",
                document_id=document_id,
                title=str(hit.get("title", "")),
                path=str(hit.get("relative_path", hit.get("title", ""))),
                language=str(hit.get("language", "")),
                language_name=str(hit.get("language_name", "")),
                categories=list(hit.get("categories", [])),
                sha256=str(hit.get("content_sha256", "")),
                score=float(hit.get("score", 0.0)),
                summary=hit.get("summary"),
            )
            self._by_id[existing.id] = existing
            self._by_document[document_id] = existing

        snippet = hit.get("snippet")
        if snippet:
            excerpt = Excerpt(
                int(hit.get("line_start", 1)), int(hit.get("line_end", 1)), str(snippet)
            )
            if all(e.line_start != excerpt.line_start for e in existing.excerpts):
                existing.excerpts.append(excerpt)
                existing.add_seen(excerpt.text)

        return existing

    def get(self, source_id: str) -> Source | None:
        return self._by_id.get(source_id)

    def all(self) -> list[Source]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
