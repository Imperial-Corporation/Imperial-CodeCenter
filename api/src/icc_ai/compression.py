"""Compression sémantique des fichiers de code joints au contexte.

Le principe n'est pas de tronquer : chaque fichier est analysé (imports,
fonctions, classes, signatures, docstrings) puis représenté à l'un des
niveaux suivants, du plus riche au plus compact :

    COMPLET  -> tout le fichier (s'il tient dans la part de budget)
    EXTRAIT  -> corps des symboles pertinents pour la requête, signatures
                des autres, imports
    PLAN     -> imports + signatures seulement
    RÉFÉRENCE-> identité du fichier seulement

Un algorithme glouton dégrade d'abord les fichiers les moins bien classés
jusqu'à respecter le budget. Si l'analyse sémantique échoue (syntaxe
invalide, langage inconnu), on retombe sur des fenêtres de lignes autour
des termes recherchés. Un bloc compressé n'est jamais présenté comme le
fichier intégral : son en-tête indique toujours le niveau et les lignes
incluses, et renvoie à `read_file` pour le contenu original.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from dataclasses import dataclass, field

from .sources import Source, normalize_space

logger = logging.getLogger(__name__)

# Niveaux de représentation, du plus complet au plus compact.
FULL, SELECTED, OUTLINE, REFERENCE = 0, 1, 2, 3
LEVEL_LABELS = {
    FULL: "COMPLET",
    SELECTED: "EXTRAIT PARTIEL",
    OUTLINE: "PLAN (signatures seulement)",
    REFERENCE: "RÉFÉRENCE (aucun code)",
}

MAX_SIGNATURE_CHARS = 200
MAX_IMPORTS = 25


def estimate_tokens(text: str) -> int:
    """Estimation prudente (≈ 3 caractères par token pour du code)."""
    return max(1, len(text) // 3)


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

@dataclass
class Symbol:
    kind: str
    name: str
    signature: str
    line_start: int  # 1-indexé, inclusif
    line_end: int
    doc: str = ""


@dataclass
class Outline:
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    header: list[str] = field(default_factory=list)


def _python_outline(text: str) -> Outline:
    tree = ast.parse(text)  # peut lever SyntaxError : géré par l'appelant
    lines = text.splitlines()
    outline = Outline()

    def signature(node) -> str:
        first_body = node.body[0].lineno if node.body else node.lineno + 1
        chunk = " ".join(
            line.strip() for line in lines[node.lineno - 1 : max(node.lineno, first_body - 1)]
        )
        return chunk[:MAX_SIGNATURE_CHARS]

    def doc_line(node) -> str:
        doc = ast.get_docstring(node) or ""
        return doc.strip().splitlines()[0][:160] if doc.strip() else ""

    def add(node, prefix: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "fonction" if not prefix else "méthode"
        elif isinstance(node, ast.ClassDef):
            kind = "classe"
        else:
            return
        outline.symbols.append(
            Symbol(kind, prefix + node.name, signature(node), node.lineno,
                   node.end_lineno or node.lineno, doc_line(node))
        )
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                add(child, prefix=f"{node.name}.")

    for node in tree.body:
        if isinstance(node, ast.Import):
            outline.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            outline.imports.append(("." * node.level) + (node.module or ""))
        else:
            add(node)

    module_doc = ast.get_docstring(tree)
    if module_doc:
        outline.header = module_doc.strip().splitlines()[:4]

    return outline


# Motifs de déclaration par famille de langages : (type, regex, groupe du nom).
_FN = r"[A-Za-z_][\w$]*"
_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "rust": [
        ("fonction", rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+({_FN})"),
        ("struct", rf"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+({_FN})"),
        ("enum", rf"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+({_FN})"),
        ("trait", rf"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+({_FN})"),
        ("impl", rf"^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>,\s]+\s+for\s+)?({_FN})"),
        ("module", rf"^\s*(?:pub\s+)?mod\s+({_FN})"),
    ],
    "js": [
        ("fonction", rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+({_FN})"),
        ("classe", rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+({_FN})"),
        ("fonction", rf"^\s*(?:export\s+)?(?:const|let|var)\s+({_FN})\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|{_FN})\s*(?::[^=]+)?=>"),
        ("interface", rf"^\s*(?:export\s+)?interface\s+({_FN})"),
        ("type", rf"^\s*(?:export\s+)?type\s+({_FN})\s*(?:<[^>]*>)?\s*="),
    ],
    "go": [
        ("fonction", rf"^func\s+(?:\([^)]*\)\s*)?({_FN})"),
        ("type", rf"^type\s+({_FN})\s+(?:struct|interface)"),
    ],
    "jvm": [  # Java, Kotlin, C#, Scala, Swift, PHP
        ("classe", rf"^\s*(?:(?:public|private|protected|internal|abstract|final|open|sealed|static|data|case)\s+)*(?:class|interface|enum|object|trait|struct|record)\s+({_FN})"),
        ("fonction", rf"^\s*(?:(?:public|private|protected|internal|static|final|abstract|override|async|open|suspend)\s+)+[\w<>\[\],.?\s]*?\b({_FN})\s*\("),
        ("fonction", rf"^\s*(?:fun|def|func|function)\s+({_FN})"),
    ],
    "c": [
        ("type", rf"^\s*(?:typedef\s+)?(?:struct|enum|union|class|namespace)\s+({_FN})"),
        ("fonction", rf"^[A-Za-z_][\w\s\*&:<>,~]*?\b({_FN})\s*\([^;{{}}]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{{?\s*$"),
    ],
    "shell": [("fonction", rf"^\s*(?:function\s+)?({_FN})\s*\(\)\s*\{{?")],
    "sql": [
        ("définition", r"^\s*create\s+(?:or\s+replace\s+)?(?:table|view|function|procedure|index|type|trigger)\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)"),
    ],
    "ruby": [
        ("classe", rf"^\s*(?:class|module)\s+({_FN})"),
        ("fonction", rf"^\s*def\s+(?:self\.)?({_FN})"),
    ],
    "lua": [("fonction", rf"^\s*(?:local\s+)?function\s+([\w.:]+)")],
    "zig": [("fonction", rf"^\s*(?:pub\s+)?(?:export\s+)?fn\s+({_FN})")],
}

_IMPORT_PATTERNS: dict[str, list[str]] = {
    "rust": [r"^\s*use\s+(.+?);"],
    "js": [r"^\s*import\s+.*?from\s+['\"]([^'\"]+)['\"]", r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", r"^\s*import\s+['\"]([^'\"]+)['\"]"],
    "go": [r"^\s*import\s+\"([^\"]+)\"", r"^\s+\"([^\"]+)\"\s*$"],
    "jvm": [r"^\s*(?:import|using|use)\s+([\w.\\*]+)"],
    "c": [r"^\s*#\s*include\s+([<\"][^>\"]+[>\"])"],
    "shell": [r"^\s*(?:source|\.)\s+(\S+)"],
    "ruby": [r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]"],
    "lua": [r"require\(?\s*['\"]([^'\"]+)['\"]"],
    "zig": [r"@import\(\s*\"([^\"]+)\"\s*\)"],
}

_FAMILY = {
    "rust": "rust",
    "javascript": "js", "typescript": "js",
    "go": "go",
    "java": "jvm", "kotlin": "jvm", "csharp": "jvm", "scala": "jvm",
    "swift": "jvm", "php": "jvm",
    "c": "c", "cpp": "c",
    "shell": "shell", "sql": "sql", "ruby": "ruby", "lua": "lua", "zig": "zig",
}

_BRACE_FAMILIES = {"rust", "js", "go", "jvm", "c", "zig"}
_MAX_SYMBOL_LINES = 400
_CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "return", "else", "match", "sizeof"}


def _brace_end(lines: list[str], start: int, limit: int) -> int:
    """Fin d'un bloc à accolades (indices 0-indexés), plafonnée à `limit`."""
    depth = 0
    seen_open = False
    for index in range(start, min(limit, start + _MAX_SYMBOL_LINES)):
        for char in lines[index]:
            if char == "{":
                depth += 1
                seen_open = True
            elif char == "}":
                depth -= 1
        if seen_open and depth <= 0:
            return index
        # Déclaration sans corps (point-virgule avant toute accolade).
        if not seen_open and lines[index].rstrip().endswith(";"):
            return index
    return min(limit, start + _MAX_SYMBOL_LINES) - 1


def _generic_outline(language: str, text: str) -> Outline:
    family = _FAMILY.get(language)
    if family is None:
        raise ValueError(f"Pas d'analyseur pour le langage « {language} ».")

    lines = text.splitlines()
    compiled = [(kind, re.compile(pattern, re.IGNORECASE if family == "sql" else 0))
                for kind, pattern in _PATTERNS.get(family, [])]
    outline = Outline()
    found: list[tuple[int, str, str]] = []  # (index, kind, name)

    for index, line in enumerate(lines):
        if len(line) > 400:
            continue
        for kind, regex in compiled:
            match = regex.match(line)
            if match and match.group(1) not in _CONTROL_WORDS:
                found.append((index, kind, match.group(1)))
                break

    for position, (index, kind, name) in enumerate(found):
        next_start = found[position + 1][0] if position + 1 < len(found) else len(lines)
        if family in _BRACE_FAMILIES:
            end = _brace_end(lines, index, len(lines))
            # Les symboles imbriqués (méthodes) restent dans leur parent.
            end = max(index, end)
        else:
            end = max(index, next_start - 1)
        outline.symbols.append(
            Symbol(kind, name, lines[index].strip()[:MAX_SIGNATURE_CHARS], index + 1, end + 1)
        )

    for pattern in _IMPORT_PATTERNS.get(family, []):
        regex = re.compile(pattern)
        for line in lines:
            match = regex.search(line)
            if match:
                outline.imports.append(match.group(1).strip())

    # En-tête : premier bloc de commentaires du fichier.
    for line in lines[:12]:
        stripped = line.strip()
        if stripped.startswith(("//", "#", "/*", "*", "--", "\"\"\"")):
            outline.header.append(stripped[:160])
        elif stripped:
            break
        if len(outline.header) >= 4:
            break

    return outline


def analyze(language: str, text: str) -> Outline:
    """Analyse sémantique d'un fichier ; lève une exception en cas d'échec."""
    if language == "python":
        return _python_outline(text)
    return _generic_outline(language, text)


# --------------------------------------------------------------------------
# Rendu
# --------------------------------------------------------------------------

@dataclass
class DocInput:
    source: Source
    content: str
    # Faux si `content` n'est qu'un extrait (lecture échouée ou tronquée).
    is_complete: bool = True


@dataclass
class CompressedBlock:
    source_id: str
    level: int
    text: str
    tokens: int
    included_ranges: list[tuple[int, int]]
    total_lines: int
    fallback: bool = False
    duplicate_of: str | None = None

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]


@dataclass
class CompressionResult:
    blocks: list[CompressedBlock]
    dropped: list[str]
    warnings: list[str]
    budget_tokens: int
    used_tokens: int

    def render(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


def _numbered(lines: list[str], start: int, end: int) -> str:
    """Lignes [start, end] (1-indexées) préfixées par leur numéro."""
    return "\n".join(f"{n}| {lines[n - 1]}" for n in range(start, end + 1))


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _term_windows(lines: list[str], terms: list[str], context: int, max_tokens: int) -> list[tuple[int, int]]:
    """Fenêtres de lignes autour des termes, plafonnées en tokens."""
    hits = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        count = sum(1 for term in terms if term in lowered)
        if count:
            hits.append((count, index + 1))

    chosen: list[tuple[int, int]] = []
    used = 0
    for _, line_no in sorted(hits, key=lambda h: (-h[0], h[1])):
        window = (max(1, line_no - context), min(len(lines), line_no + context))
        cost = estimate_tokens("\n".join(lines[window[0] - 1 : window[1]])) + 6
        if used + cost > max_tokens:
            continue
        chosen.append(window)
        used += cost
    return _merge_ranges(chosen)


def _symbol_relevance(symbol: Symbol, body: str, terms: list[str]) -> float:
    name = symbol.name.lower()
    lowered = body.lower()
    score = 0.0
    for term in terms:
        if term in name:
            score += 3.0
        occurrences = lowered.count(term)
        if occurrences:
            score += 1.0 + min(occurrences, 10) * 0.2
        if term in symbol.doc.lower():
            score += 1.0
    return score


def _body_hash(text: str) -> str:
    return hashlib.sha1(normalize_space(text).encode()).hexdigest()


class _Renderer:
    """Produit les représentations d'un fichier à chaque niveau."""

    def __init__(self, doc: DocInput, terms: list[str], body_budget: int,
                 cap_tokens: int | None = None):
        self.doc = doc
        # Plafond du corps d'un bloc PLAN/EXTRAIT : au-delà, les signatures
        # les moins pertinentes sont remplacées par un décompte.
        self.cap_tokens = cap_tokens
        self.source = doc.source
        self.lines = doc.content.splitlines()
        self.terms = terms
        self.body_budget = body_budget
        self.fallback = False
        self.outline: Outline | None = None

        try:
            self.outline = analyze(doc.source.language, doc.content)
        except Exception as exc:  # SyntaxError, ValueError, bug d'analyseur…
            self.fallback = True
            logger.info("Analyse sémantique impossible pour %s : %s", doc.source.id, type(exc).__name__)

    # -- en-tête -----------------------------------------------------------

    def header(self, level: int, ranges: list[tuple[int, int]], duplicates: list[str]) -> str:
        s = self.source
        total = len(self.lines)
        complete = level == FULL and self.doc.is_complete
        shown = ", ".join(f"{a}-{b}" for a, b in ranges[:5]) if ranges else "aucune"
        if len(ranges) > 5:
            shown += f" … (+{len(ranges) - 5} plages)"
        extra = f" | identique à : {', '.join(duplicates)}" if duplicates else ""
        scope = f"lignes {shown} sur {total}" if level != REFERENCE else f"{total} lignes"
        note = "" if complete else " | CONTENU PARTIEL : ne pas le prendre pour le fichier intégral"
        return (
            f"=== SOURCE {s.id} | {s.path} | {s.language} | sha256:{s.sha256[:12]} | "
            f"{LEVEL_LABELS[level]} ({scope}){extra}{note} ==="
        )

    def footer(self, level: int) -> str:
        if level == FULL and self.doc.is_complete:
            return ""
        return f"(→ read_file(source_id=\"{self.source.id}\", start_line, end_line) donne le code original)"

    # -- niveaux ------------------------------------------------------------

    def imports_line(self) -> str:
        if not self.outline or not self.outline.imports:
            return ""
        unique = list(dict.fromkeys(self.outline.imports))
        more = f" (+{len(unique) - MAX_IMPORTS})" if len(unique) > MAX_IMPORTS else ""
        return "dépendances : " + ", ".join(unique[:MAX_IMPORTS]) + more

    def render(self, level: int, seen: dict[str, tuple[str, str]] | None = None
               ) -> tuple[str, list[tuple[int, int]]]:
        """Retourne (corps du bloc, lignes incluses)."""
        total = len(self.lines)

        if level == REFERENCE:
            parts = [self.source.summary or ""]
            if self.outline and self.outline.symbols:
                names = ", ".join(s.name for s in self.outline.symbols[:15])
                parts.append(f"symboles : {names}")
            return "\n".join(p for p in parts if p), []

        if level == FULL:
            return _numbered(self.lines, 1, total) if total else "", ([(1, total)] if total else [])

        if self.outline is None:
            return self._render_fallback(level)

        if level == OUTLINE:
            return self._render_outline(set(), seen)

        return self._render_selected(seen)

    def _symbol_lines(self, symbol: Symbol) -> list[str]:
        return self.lines[symbol.line_start - 1 : symbol.line_end]

    def _render_outline(self, with_body: set[int], seen) -> tuple[str, list[tuple[int, int]]]:
        head: list[str] = []
        imports = self.imports_line()
        if imports:
            head.append(imports)
        if self.outline and self.outline.header:
            head.append("en-tête : " + " ".join(self.outline.header)[:300])

        # Symboles retenus (un symbole imbriqué dans un corps déjà affiché
        # n'est pas répété) : (index, symbole, avec corps ?).
        entries: list[tuple[int, Symbol, bool]] = []
        covered_until = 0
        for index, symbol in enumerate(self.outline.symbols if self.outline else []):
            if symbol.line_start <= covered_until:
                continue
            is_body = index in with_body
            entries.append((index, symbol, is_body))
            if is_body:
                covered_until = symbol.line_end

        # Signatures conservées : toutes si aucun plafond, sinon les plus
        # pertinentes pour la requête dans la limite du budget.
        keep = {i for i, _, is_body in entries if is_body}
        omitted: list[Symbol] = []
        signatures = [(i, sym) for i, sym, is_body in entries if not is_body]

        if self.cap_tokens is None:
            keep.update(i for i, _ in signatures)
        else:
            used = estimate_tokens("\n".join(head))
            for i, sym, is_body in entries:
                if is_body:
                    used += estimate_tokens(
                        "\n".join(self._symbol_lines(sym))[: self.body_budget * 3 + 400]
                    )
            ranked = sorted(
                signatures,
                key=lambda e: (-_symbol_relevance(e[1], e[1].signature, self.terms), e[0]),
            )
            reserve = 40  # ligne « autres symboles omis »
            for i, sym in ranked:
                cost = estimate_tokens(self._signature_line(sym)) + 1
                if used + cost + reserve > self.cap_tokens:
                    omitted.append(sym)
                    continue
                keep.add(i)
                used += cost
            omitted.sort(key=lambda sym: sym.line_start)

        out: list[str] = list(head)
        ranges: list[tuple[int, int]] = []

        for index, symbol, is_body in entries:
            if index not in keep:
                continue
            if is_body:
                body = "\n".join(self._symbol_lines(symbol))
                digest = _body_hash(body)
                if seen is not None and digest in seen and seen[digest][0] != self.source.id:
                    other_id, other_name = seen[digest]
                    out.append(
                        f"{symbol.line_start}| {symbol.signature}  ⋯ corps identique à {other_id}:{other_name}"
                    )
                    ranges.append((symbol.line_start, symbol.line_start))
                else:
                    if seen is not None:
                        seen[digest] = (self.source.id, symbol.name)
                    out.append(_numbered(self.lines, symbol.line_start, symbol.line_end))
                    ranges.append((symbol.line_start, symbol.line_end))
            else:
                out.append(self._signature_line(symbol))
                ranges.append((symbol.line_start, symbol.line_start))

        if omitted:
            names = ", ".join(sym.name for sym in omitted[:12])
            more = f" (+{len(omitted) - 12})" if len(omitted) > 12 else ""
            out.append(f"⋯ {len(omitted)} autre(s) symbole(s) non détaillé(s) : {names}{more}")

        return "\n".join(out), _merge_ranges(ranges)

    @staticmethod
    def _signature_line(symbol: Symbol) -> str:
        doc = f"  # {symbol.doc}" if symbol.doc else ""
        return (
            f"{symbol.line_start}| {symbol.signature}{doc}"
            f"  ⋯ corps omis (lignes {symbol.line_start}-{symbol.line_end})"
        )

    def _render_selected(self, seen) -> tuple[str, list[tuple[int, int]]]:
        symbols = self.outline.symbols
        scored = []
        for index, symbol in enumerate(symbols):
            body = "\n".join(self._symbol_lines(symbol))
            relevance = _symbol_relevance(symbol, body, self.terms)
            if relevance > 0:
                scored.append((relevance, index, estimate_tokens(body)))

        chosen: set[int] = set()
        used = 0
        for _, index, cost in sorted(scored, key=lambda s: (-s[0], s[1])):
            if used + cost > self.body_budget:
                continue
            chosen.add(index)
            used += cost

        if not chosen and self.terms:
            # Aucun symbole pertinent (code hors fonction, termes en commentaire) :
            # fenêtres autour des termes, en plus du plan.
            text, ranges = self._render_outline(set(), seen)
            windows = _term_windows(self.lines, self.terms, 4, self.body_budget)
            if windows:
                text += "\n" + "\n⋯\n".join(_numbered(self.lines, a, b) for a, b in windows)
                ranges = _merge_ranges(ranges + windows)
            return text, ranges

        return self._render_outline(chosen, seen)

    def _render_fallback(self, level: int) -> tuple[str, list[tuple[int, int]]]:
        """Repli sans analyse : début du fichier et fenêtres autour des termes."""
        total = len(self.lines)
        head_n = 15 if level == OUTLINE else 10
        ranges = [(1, min(total, head_n))]

        if level == SELECTED:
            ranges += _term_windows(self.lines, self.terms, 5, self.body_budget)

        merged = _merge_ranges(ranges)
        text = "\n⋯\n".join(_numbered(self.lines, a, b) for a, b in merged)
        return text, merged


def _build_block(renderer: _Renderer, level: int, duplicates: list[str],
                 seen: dict[str, tuple[str, str]] | None) -> CompressedBlock:
    body, ranges = renderer.render(level, seen)
    header = renderer.header(level, ranges, duplicates)
    footer = renderer.footer(level)
    text = "\n".join(part for part in (header, body, footer) if part)

    return CompressedBlock(
        source_id=renderer.source.id,
        level=level,
        text=text,
        tokens=estimate_tokens(text),
        included_ranges=ranges,
        total_lines=len(renderer.lines),
        fallback=renderer.fallback,
    )


def compress_documents(
    docs: list[DocInput], terms: list[str], budget_tokens: int
) -> CompressionResult:
    """Compresse des documents classés par pertinence décroissante.

    `docs` doit être trié du plus au moins pertinent. Le résultat respecte
    `budget_tokens` (estimation) en dégradant d'abord les derniers.
    """
    warnings: list[str] = []
    dropped: list[str] = []

    # 1. Déduplication par empreinte : on garde le mieux classé.
    kept: list[DocInput] = []
    duplicates: dict[str, list[str]] = {}
    by_hash: dict[str, str] = {}
    for doc in docs:
        digest = doc.source.sha256 or _body_hash(doc.content)
        if digest in by_hash:
            duplicates.setdefault(by_hash[digest], []).append(doc.source.id)
            continue
        by_hash[digest] = doc.source.id
        kept.append(doc)

    if not kept:
        return CompressionResult([], [], warnings, budget_tokens, 0)

    share = max(300, budget_tokens // len(kept))
    renderers = [
        _Renderer(doc, terms, body_budget=int(share * 0.6), cap_tokens=max(150, share - 130))
        for doc in kept
    ]

    for renderer in renderers:
        if renderer.fallback:
            warnings.append(
                f"Analyse sémantique indisponible pour {renderer.source.id} : repli sur des extraits."
            )

    # 2. Choix des niveaux : tout au plus riche, puis dégradation gloutonne.
    levels = [FULL] * len(renderers)

    def cost(i: int) -> int:
        return _build_block(renderers[i], levels[i], duplicates.get(renderers[i].source.id, []), None).tokens

    costs = [cost(i) for i in range(len(renderers))]

    def total() -> int:
        return sum(costs[i] for i in range(len(renderers)) if levels[i] is not None)

    while total() > budget_tokens:
        # Candidats : fichiers encore dégradables, les moins bien classés d'abord.
        candidates = [i for i in range(len(renderers)) if levels[i] is not None and levels[i] < REFERENCE]
        if candidates:
            # Dégrade le moins bien classé parmi ceux qui ont le niveau
            # le plus riche : les meilleurs résultats restent détaillés.
            richest = min(levels[i] for i in candidates)
            victim = max(i for i in candidates if levels[i] == richest)
            levels[victim] += 1
            costs[victim] = cost(victim)
            continue

        # Tout est déjà en RÉFÉRENCE : on retire les derniers documents.
        remaining = [i for i in range(len(renderers)) if levels[i] is not None]
        if len(remaining) <= 1:
            break
        last = remaining[-1]
        dropped.append(renderers[last].source.id)
        levels[last] = None

    # 3. Rendu final avec déduplication des corps identiques entre fichiers.
    seen: dict[str, tuple[str, str]] = {}
    blocks: list[CompressedBlock] = []
    for i, renderer in enumerate(renderers):
        if levels[i] is None:
            continue
        block = _build_block(renderer, levels[i], duplicates.get(renderer.source.id, []), seen)
        blocks.append(block)

    if dropped:
        warnings.append(
            "Budget de contexte insuffisant : sources non jointes (" + ", ".join(dropped) + ")."
        )
    if any(b.level != FULL for b in blocks):
        warnings.append(
            "Certains fichiers sont compressés (extraits ou plans) : ce ne sont pas des contenus intégraux."
        )

    return CompressionResult(blocks, dropped, warnings, budget_tokens, sum(b.tokens for b in blocks))
