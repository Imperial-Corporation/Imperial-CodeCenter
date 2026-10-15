"""Classement automatique d'un fichier dans une catégorie de la bibliothèque.

Les catégories sont celles des exemples livrés avec CodeCenter ; elles
deviennent des dossiers (donc des tags) dans le dossier surveillé. Le
classement est heuristique : mots-clés du chemin et des topics du dépôt
(poids 3), puis description, imports et API reconnaissables dans le contenu (poids 1,
chaque mot-clé compte une fois).
"""

from __future__ import annotations

import re

DEFAULT_CATEGORY = "divers"

# catégorie -> (mots-clés de chemin/topics, motifs de contenu)
RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "api": (
        ("api", "rest", "server", "router", "routes", "endpoint", "graphql", "grpc", "http", "webhook"),
        (r"\bfastapi\b", r"\bflask\b", r"\bexpress\(", r"net/http", r"http\.server", r"\baxum\b",
         r"\bactix\b", r"@app\.(get|post|route)", r"\bhttp\.createServer\b", r"\bRestController\b"),
    ),
    "algorithmes": (
        ("algorithm", "algorithms", "sort", "sorting", "graph", "tree", "search", "dynamic-programming",
         "leetcode", "data-structures", "structures"),
        (r"\bbinary.?search\b", r"\bdijkstra\b", r"\bquick.?sort\b", r"\bmerge.?sort\b",
         r"\blinked.?list\b", r"\bheap\b", r"\bbfs\b", r"\bdfs\b", r"\bmemoiz"),
    ),
    "fichiers-et-donnees": (
        ("data", "database", "db", "sql", "csv", "json", "parser", "parse", "etl", "storage", "file", "files", "io"),
        (r"\bsqlite3?\b", r"\bcsv\b", r"\bjson\.(load|dump)", r"\bBufReader\b", r"\bos\.ReadFile\b",
         r"\bSELECT\b.+\bFROM\b", r"\bCREATE TABLE\b", r"\bfs\.(readFile|writeFile)"),
    ),
    "web": (
        ("web", "frontend", "ui", "css", "html", "dom", "component", "components", "browser", "static"),
        (r"document\.(querySelector|getElementById)", r"addEventListener\(", r"<!doctype html",
         r"display:\s*(flex|grid)", r"\bReact\b", r"\bVue\b", r"@media"),
    ),
    "systeme": (
        ("system", "os", "cli", "shell", "process", "daemon", "kernel", "signal", "sysadmin", "devops", "tools"),
        (r"\bsubprocess\b", r"\bsigaction\b", r"\bfork\(", r"\bexecve?\b", r"std::process",
         r"\bargparse\b", r"\bos\.Args\b", r"#!/usr/bin/env (ba)?sh", r"\bsystemctl\b"),
    ),
    "concurrence": (
        ("async", "concurrency", "concurrent", "parallel", "thread", "threads", "worker", "actor", "queue"),
        (r"\basyncio\b", r"\bgo func\b", r"\bgoroutine\b", r"\btokio\b", r"std::thread",
         r"\bThreadPool", r"\bsync\.WaitGroup\b", r"\bPromise\.all\b", r"\bmpsc\b", r"\bcoroutine"),
    ),
    "configuration": (
        ("config", "configuration", "settings", "docker", "deploy", "deployment", "ci", "env", "infra"),
        (r"\bdocker\b", r"(?m)^(?-i:FROM) \S+", r"\bapiVersion:", r"\bterraform\b", r"\[tool\.\w+\]",
         r"\bdotenv\b", r"\bos\.environ\b", r"\bprocess\.env\b"),
    ),
}

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_COMPILED = {name: [re.compile(p, re.IGNORECASE) for p in patterns] for name, (_, patterns) in RULES.items()}


def classify(path: str, topics: list[str] | None = None, description: str = "", content: str = "") -> str:
    """Retourne le nom de la catégorie la plus probable (`divers` par défaut)."""
    words = set(_WORD_SPLIT.split(path.lower()))
    for topic in topics or []:
        words.update(_WORD_SPLIT.split(topic.lower()))
        words.add(topic.lower())
    # Les mots de la description sont un indice faible (poids 1, comme le contenu).
    description_words = set(_WORD_SPLIT.split(description.lower())) - words

    sample = content[:20_000]
    best_name, best_score = DEFAULT_CATEGORY, 0

    for name, (keywords, _) in RULES.items():
        score = 3 * sum(1 for keyword in keywords if keyword in words)
        score += sum(1 for keyword in keywords if keyword in description_words)
        score += sum(1 for pattern in _COMPILED[name] if pattern.search(sample))
        if score > best_score:
            best_name, best_score = name, score

    # Un seul indice faible (un mot de description) ne suffit pas.
    return best_name if best_score >= 2 else DEFAULT_CATEGORY
