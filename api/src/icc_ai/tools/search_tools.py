"""Outils reliés aux fonctions Rust existantes (`search`, `get_document`,
`list_filters`). Aucun outil ne reçoit de chemin ni d'identifiant de base :
seuls les identifiants de source du serveur (`S1`…) sont acceptés.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import Tool, ToolContext, ToolError, dumps_compact, require_source

_FILTER_RE = r"^[A-Za-z0-9][A-Za-z0-9 _.+#\-/]{0,98}$"


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=2, max_length=300,
        description="Mots-clés ou description du code recherché.",
    )
    limit: int = Field(default=5, ge=1, le=10, description="Nombre de résultats (1 à 10).")
    language: str | None = Field(
        default=None, pattern=_FILTER_RE,
        description="Filtre langage (slug, ex. « rust », « python »). Voir list_filters.",
    )
    category: str | None = Field(
        default=None, pattern=_FILTER_RE,
        description="Filtre catégorie (slug). Voir list_filters.",
    )

    @field_validator("query")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("La requête est trop courte.")
        return value


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Cherche des fichiers de code et de documentation indexés. Retourne, pour chaque "
        "résultat, un identifiant de source (S1, S2…), son chemin, son langage et un extrait. "
        "Utilise-le pour approfondir ou élargir la recherche, avec d'autres mots-clés ou filtres."
    )
    args_model = SearchCodeArgs

    async def run(self, args: SearchCodeArgs, ctx: ToolContext) -> str:
        limit = min(args.limit, ctx.limits.max_search_results)
        data = await ctx.engine.search(args.query, limit, args.language, args.category)

        hits = data.get("results") or []
        if not hits:
            return "Aucun résultat pour cette recherche. Essaie d'autres mots-clés ou retire un filtre."

        lines = [f"{len(hits)} résultat(s) :"]
        for hit in hits:
            source = ctx.sources.register_hit(hit)
            snippet = str(hit.get("snippet") or "")[: ctx.limits.snippet_chars]
            lines.append(
                f"\n[{source.id}] {source.path} | {source.language or '?'} | score {source.score:g}"
                f" | lignes {hit.get('line_start', 1)}-{hit.get('line_end', 1)}"
            )
            if source.summary:
                lines.append(f"résumé : {str(source.summary)[:300]}")
            if snippet:
                lines.append(snippet)

        if data.get("truncated"):
            lines.append("\n(note : la recherche a atteint un plafond, certains fichiers n'ont pas été analysés)")
        return "\n".join(lines)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^S\d{1,3}$", description="Identifiant de source (ex. S2).")
    start_line: int | None = Field(default=None, ge=1, le=10_000_000, description="Première ligne (1-indexée).")
    end_line: int | None = Field(default=None, ge=1, le=10_000_000, description="Dernière ligne incluse.")

    @field_validator("end_line")
    @classmethod
    def _order(cls, value, info):
        start = info.data.get("start_line")
        if value is not None and start is not None and value < start:
            raise ValueError("end_line doit être supérieur ou égal à start_line.")
        return value


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Lit le contenu original d'un fichier déjà trouvé (par son identifiant de source), "
        "éventuellement une plage de lignes. Le texte est numéroté ligne par ligne. "
        "La taille retournée est plafonnée : lis par plages pour les gros fichiers."
    )
    args_model = ReadFileArgs

    async def run(self, args: ReadFileArgs, ctx: ToolContext) -> str:
        source = require_source(ctx, args.source_id)
        data = await ctx.engine.get_document(
            source.document_id, args.start_line, args.end_line, ctx.limits.max_read_bytes
        )

        content = str(data.get("content", ""))
        line_start = int(data.get("line_start", 1))
        raw_lines = content.splitlines()
        numbered = "\n".join(f"{line_start + i}| {line}" for i, line in enumerate(raw_lines))
        source.add_seen(content)

        line_end = int(data.get("line_end", line_start + max(len(raw_lines) - 1, 0)))
        total = int(data.get("total_lines", line_end))
        header = f"[{source.id}] {source.path} | lignes {line_start}-{line_end} sur {total}"

        notes = []
        if data.get("truncated"):
            notes.append(
                f"contenu coupé : relis à partir de la ligne {line_end + 1} pour la suite"
            )
        if data.get("content_matches_index") is False:
            notes.append("le fichier a changé depuis son indexation")
        footer = f"\n({' ; '.join(notes)})" if notes else ""

        return f"{header}\n{numbered}{footer}" if numbered else f"{header}\n(plage vide)"


class FileInfoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^S\d{1,3}$", description="Identifiant de source (ex. S2).")


class FileInfoTool(Tool):
    name = "get_file_info"
    description = (
        "Donne les métadonnées d'un fichier trouvé : chemin, langage, catégories, taille, "
        "résumé, symboles et dépendances connus, nombre de lignes. Ne retourne pas le code."
    )
    args_model = FileInfoArgs

    async def run(self, args: FileInfoArgs, ctx: ToolContext) -> str:
        source = require_source(ctx, args.source_id)
        # Lecture minimale : seules les métadonnées nous intéressent.
        data = await ctx.engine.get_document(source.document_id, 1, 1, 1)

        info = {
            "source_id": source.id,
            "chemin": source.path,
            "langage": source.language_name or source.language,
            "categories": source.categories,
            "taille_octets": data.get("file_size"),
            "lignes": data.get("total_lines"),
            "sha256": str(data.get("content_sha256", ""))[:16],
            "modifie_depuis_indexation": data.get("content_matches_index") is False,
            "resume": data.get("summary"),
            "symboles": _bounded(data.get("symbols"), 40),
            "dependances": _bounded(data.get("dependencies"), 30),
        }
        return dumps_compact(info)


def _bounded(value, limit: int):
    if isinstance(value, list):
        return value[:limit]
    return value or []


class ListFiltersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFiltersTool(Tool):
    name = "list_filters"
    description = (
        "Liste les langages et catégories réellement présents dans l'index, avec leur nombre "
        "de documents. Utile pour choisir un filtre valide de search_code."
    )
    args_model = ListFiltersArgs

    async def run(self, args: ListFiltersArgs, ctx: ToolContext) -> str:
        data = await ctx.engine.list_filters()
        languages = [
            f"{item.get('slug')} ({item.get('documents')})"
            for item in (data.get("languages") or [])[:40]
        ]
        categories = [
            f"{item.get('slug')} ({item.get('documents')})"
            for item in (data.get("categories") or [])[:60]
        ]
        if not languages and not categories:
            raise ToolError("L'index ne contient aucun langage ni catégorie.")
        return (
            "langages : " + (", ".join(languages) or "aucun")
            + "\ncatégories : " + (", ".join(categories) or "aucune")
        )
