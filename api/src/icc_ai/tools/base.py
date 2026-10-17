# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Interface des outils IA, indépendante du fournisseur.

Un outil déclare ses arguments avec un modèle pydantic (`extra="forbid"`) :
les arguments proposés par le modèle sont donc validés strictement avant
toute exécution. Les outils n'exécutent jamais de code arbitraire et ne
manipulent que des identifiants de source (`S1`, `S2`…) attribués par le
serveur : le modèle ne peut ni fournir de chemin, ni viser un document qui
n'a pas été réellement récupéré.
"""

from __future__ import annotations

import abc
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..engine_gateway import EngineGateway
from ..errors import EngineError
from ..providers.base import ToolCall, ToolSpec
from ..sources import SOURCE_ID_RE, Source, SourceRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolLimits:
    """Plafonds appliqués à tous les outils d'une recherche."""

    max_result_chars: int = 9000   # taille maximale d'un résultat renvoyé au modèle
    max_read_bytes: int = 24_000   # octets lus par `read_file`
    max_search_results: int = 8    # résultats par appel de `search_code`
    snippet_chars: int = 700       # taille d'un extrait dans les résultats


@dataclass
class ToolContext:
    """État partagé par les outils pendant une recherche."""

    engine: EngineGateway
    sources: SourceRegistry
    limits: ToolLimits = field(default_factory=ToolLimits)


@dataclass
class ToolExecution:
    """Trace d'un appel d'outil (sans contenu de fichier ni secret)."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    result_chars: int
    error: str | None = None


@dataclass
class ToolOutcome:
    content: str
    execution: ToolExecution


class Tool(abc.ABC):
    """Outil exposé au modèle."""

    name: str
    description: str
    args_model: type[BaseModel]

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        return ToolSpec(self.name, self.description, schema)

    @abc.abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> str:
        """Exécute l'outil avec des arguments déjà validés ; retourne du texte."""


class ToolError(Exception):
    """Erreur d'usage d'un outil : le message est renvoyé au modèle."""


def require_source(ctx: ToolContext, source_id: str) -> Source:
    """Retrouve une source du registre ; refuse tout autre identifiant."""
    if not SOURCE_ID_RE.match(source_id):
        raise ToolError("Identifiant de source invalide (attendu : S1, S2…).")
    source = ctx.sources.get(source_id)
    if source is None:
        raise ToolError(
            f"Source {source_id} inconnue : utilise search_code pour obtenir des identifiants."
        )
    return source


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n[… résultat tronqué à {limit} caractères]"


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Version bornée des arguments, pour la trace."""
    out: dict[str, Any] = {}
    for key, value in list(arguments.items())[:8]:
        out[str(key)[:40]] = value if not isinstance(value, str) else value[:120]
    return out


class ToolRegistry:
    """Registre et exécuteur d'outils, avec limite d'appels par recherche."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Outil déjà enregistré : {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutcome:
        """Exécute un appel : ne lève jamais, sauf annulation asyncio.

        Toute erreur (outil inconnu, arguments invalides, échec du moteur)
        devient un texte renvoyé au modèle pour qu'il puisse se corriger.
        """
        started = time.perf_counter()
        ok, error = True, None
        content = ""

        tool = self._tools.get(call.name)
        try:
            if tool is None:
                raise ToolError(
                    f"Outil inconnu : {call.name[:60]}. Outils disponibles : "
                    + ", ".join(self._tools)
                )
            if call.arguments_error:
                raise ToolError(call.arguments_error)

            try:
                args = tool.args_model.model_validate(call.arguments)
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'arguments'} : {e['msg']}"
                    for e in exc.errors()[:5]
                )
                raise ToolError(f"Arguments invalides : {details}") from exc

            content = await tool.run(args, ctx)
        except ToolError as exc:
            ok, error, content = False, str(exc), f"ERREUR : {exc}"
        except EngineError as exc:
            ok, error, content = False, exc.message, f"ERREUR : {exc.message}"
        except Exception as exc:  # bug d'outil : ne doit pas faire échouer la recherche
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.exception("Échec inattendu de l'outil %s", call.name)
            ok, error = False, "Erreur interne de l'outil."
            content = "ERREUR : erreur interne de l'outil."

        content = _truncate(content, ctx.limits.max_result_chars)
        execution = ToolExecution(
            name=call.name[:60],
            arguments=_summarize_arguments(call.arguments),
            ok=ok,
            duration_ms=int((time.perf_counter() - started) * 1000),
            result_chars=len(content),
            error=error,
        )
        return ToolOutcome(content, execution)


def dumps_compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
