# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Format de réponse stable entre le backend et le frontend.

Deux familles de données sont strictement séparées :

* `answer` : contenu GÉNÉRÉ par le modèle (synthèse, sections, code), validé
  et borné ;
* `sources`, `meta`, `warnings` : métadonnées FIABLES produites par le
  serveur, jamais par le modèle.

Le modèle ne désigne les sources que par leurs identifiants (`S1`…). Tout
identifiant absent du registre est retiré. Un bloc de code n'est marqué
`verified` que si son texte figure réellement dans une source citée.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .sources import SOURCE_ID_RE, SourceRegistry, normalize_space

MAX_SUMMARY = 4000
MAX_SECTIONS = 8
MAX_BODY = 6000
MAX_CODE = 12000
MAX_BLOCKS_PER_SECTION = 6
MAX_WARNINGS = 6
MAX_FOLLOW_UPS = 4
MAX_FOLLOW_UP_CHARS = 140

_LANG_RE = re.compile(r"^[A-Za-z0-9_+#.\-]{1,30}$")


# --------------------------------------------------------------------------
# Sortie du modèle (non fiable, validée)
# --------------------------------------------------------------------------

class ModelCodeBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = ""
    code: str = Field(max_length=MAX_CODE)
    caption: str | None = Field(default=None, max_length=300)
    source_ids: list[str] = Field(default_factory=list, max_length=10)


class ModelSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=MAX_BODY)
    code_blocks: list[ModelCodeBlock] = Field(default_factory=list, max_length=MAX_BLOCKS_PER_SECTION)


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    sections: list[ModelSection] = Field(default_factory=list, max_length=MAX_SECTIONS)
    used_sources: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=MAX_WARNINGS)
    # Pistes d'approfondissement proposées à l'utilisateur (questions courtes).
    follow_ups: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("follow_ups", mode="before")
    @classmethod
    def _lenient_follow_ups(cls, value):
        """Les pistes sont facultatives : une piste mal formée ou trop nombreuse ne doit
        jamais faire perdre toute la réponse structurée."""
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)][:12]


# --------------------------------------------------------------------------
# Réponse envoyée au frontend
# --------------------------------------------------------------------------

class CodeBlockOut(BaseModel):
    language: str
    code: str
    caption: str | None = None
    source_ids: list[str]
    # Vrai si le code figure littéralement dans une source citée et vue.
    verified: bool


class SectionOut(BaseModel):
    title: str
    body: str
    code_blocks: list[CodeBlockOut]


class AnswerOut(BaseModel):
    summary: str
    sections: list[SectionOut]
    # Avertissements formulés par le modèle (affichés comme tels, non fiables).
    model_notes: list[str] = []
    # Questions de suite suggérées par le modèle (texte brut, bornées).
    follow_ups: list[str] = []


class ExcerptOut(BaseModel):
    line_start: int
    line_end: int
    text: str


class SourceOut(BaseModel):
    """Métadonnées d'une source, issues du moteur (fiables)."""

    id: str
    document_id: str
    title: str
    path: str
    language: str
    language_name: str
    categories: list[str]
    sha256: str
    score: float
    summary: str | None
    excerpts: list[ExcerptOut]
    cited: bool


class ToolTraceOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    result_chars: int
    error: str | None = None


class MetaOut(BaseModel):
    provider: str | None = None
    model: str | None = None
    took_ms: int = 0
    tool_calls: int = 0
    tools_enabled: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    context_budget_tokens: int = 0
    context_used_tokens: int = 0
    sources_compressed: int = 0
    sources_dropped: int = 0
    # Pour une future conversation : identifiant de cette exécution.
    run_id: str = ""
    # Conversation à laquelle appartient cette réponse (vide hors chat).
    conversation_id: str = ""
    # Niveau de profondeur retenu : overview | focused | deep.
    depth: str = ""


class SearchResponse(BaseModel):
    # ok : réponse IA ; search_only : aucun fournisseur actif, résultats bruts ;
    # no_results : rien trouvé dans l'index.
    status: Literal["ok", "search_only", "no_results"]
    query: str
    answer: AnswerOut | None
    sources: list[SourceOut]
    warnings: list[str]
    tool_trace: list[ToolTraceOut] = []
    meta: MetaOut = MetaOut()


# --------------------------------------------------------------------------
# Analyse de la réponse brute du modèle
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


def _extract_json(text: str) -> Any | None:
    """Premier objet JSON de `text` (tolère les clôtures ``` et le bavardage)."""
    candidate = text.strip()
    fenced = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)

    decoder = json.JSONDecoder()
    start = candidate.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(candidate[start:])
        except ValueError:
            start = candidate.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = candidate.find("{", start + 1)
    return None


def _fallback_from_text(text: str) -> ModelAnswer:
    """Réponse non structurée : texte libre + blocs ``` extraits."""
    blocks: list[ModelCodeBlock] = []

    def take(match: re.Match) -> str:
        if len(blocks) < MAX_BLOCKS_PER_SECTION:
            lang = match.group(1).strip()
            blocks.append(
                ModelCodeBlock(
                    language=lang if _LANG_RE.match(lang) else "",
                    code=match.group(2).rstrip("\n")[:MAX_CODE],
                )
            )
        return ""

    prose = _FENCE_RE.sub(take, text).strip() or "Voir les blocs de code ci-dessous."
    summary = prose[:MAX_SUMMARY]
    sections = [ModelSection(title="Code", code_blocks=blocks)] if blocks else []
    return ModelAnswer(summary=summary, sections=sections)


def parse_model_output(text: str) -> tuple[ModelAnswer, list[str]]:
    """Valide la sortie du modèle ; ne lève pas d'exception.

    Retourne (réponse, avertissements serveur). Une sortie invalide devient
    un texte libre signalé comme tel plutôt qu'une erreur.
    """
    warnings: list[str] = []
    data = _extract_json(text)

    if data is not None:
        try:
            return ModelAnswer.model_validate(data), warnings
        except ValidationError:
            warnings.append(
                "La réponse du modèle ne respectait pas le format attendu : "
                "elle est affichée sans structure."
            )
    else:
        warnings.append(
            "Le modèle n'a pas répondu au format structuré attendu : "
            "sa réponse est affichée telle quelle."
        )

    return _fallback_from_text(text), warnings


# --------------------------------------------------------------------------
# Construction de la réponse finale
# --------------------------------------------------------------------------

def _clean_language(value: str) -> str:
    value = value.strip().lower()
    return value if _LANG_RE.match(value) else ""


def _clean_follow_ups(items: list[str]) -> list[str]:
    """Pistes de suite : texte brut, sans doublon, courtes, au plus 4."""
    result: list[str] = []
    for raw in items:
        text = " ".join(str(raw).split())[:MAX_FOLLOW_UP_CHARS].strip()
        if text and text.lower() not in (r.lower() for r in result):
            result.append(text)
    return result[:MAX_FOLLOW_UPS]


def build_answer(
    model_answer: ModelAnswer, registry: SourceRegistry
) -> tuple[AnswerOut, set[str], list[str]]:
    """Croise la sortie du modèle avec le registre des sources réelles.

    Retourne (réponse, identifiants cités valides, avertissements).
    """
    warnings: list[str] = []
    unknown: set[str] = set()
    cited: set[str] = set()

    def valid_ids(ids: list[str]) -> list[str]:
        result = []
        for raw in ids:
            source_id = str(raw).strip().upper()
            if SOURCE_ID_RE.match(source_id) and registry.get(source_id):
                if source_id not in result:
                    result.append(source_id)
            else:
                unknown.add(str(raw)[:20])
        return result

    cited.update(valid_ids(model_answer.used_sources))

    sections: list[SectionOut] = []
    for section in model_answer.sections:
        blocks: list[CodeBlockOut] = []
        for block in section.code_blocks:
            ids = valid_ids(block.source_ids)
            cited.update(ids)
            haystack = normalize_space(" ".join(registry.get(i).seen_text for i in ids))
            needle = normalize_space(block.code)
            blocks.append(
                CodeBlockOut(
                    language=_clean_language(block.language),
                    code=block.code,
                    caption=block.caption,
                    source_ids=ids,
                    verified=bool(ids and needle and needle in haystack),
                )
            )
        sections.append(SectionOut(title=section.title, body=section.body, code_blocks=blocks))

    if unknown:
        warnings.append(
            "Références ignorées car absentes des résultats réels : "
            + ", ".join(sorted(unknown)) + "."
        )
    if len(registry) and not cited:
        warnings.append("Le modèle n'a cité aucune source : vérifie sa réponse avec les résultats ci-dessous.")

    answer = AnswerOut(
        summary=model_answer.summary.strip(),
        sections=sections,
        model_notes=[w[:300] for w in model_answer.warnings],
        follow_ups=_clean_follow_ups(model_answer.follow_ups),
    )
    return answer, cited, warnings


def sources_out(registry: SourceRegistry, cited: set[str]) -> list[SourceOut]:
    """Sources du serveur, citées d'abord (ordre d'identifiant conservé)."""
    items = [
        SourceOut(
            id=s.id,
            document_id=s.document_id,
            title=s.title,
            path=s.path,
            language=s.language,
            language_name=s.language_name,
            categories=s.categories,
            sha256=s.sha256,
            score=s.score,
            summary=s.summary,
            excerpts=[ExcerptOut(line_start=e.line_start, line_end=e.line_end, text=e.text)
                      for e in s.excerpts],
            cited=s.id in cited,
        )
        for s in registry.all()
    ]
    return sorted(items, key=lambda s: (not s.cited, int(s.id[1:])))
