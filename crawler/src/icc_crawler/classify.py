# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Classement automatique d'un fichier : catégorie, sous-catégorie et tags.

Les catégories (et sous-catégories) deviennent des dossiers imbriqués dans le
dossier surveillé (`<catégorie>/<sous-catégorie>/<langage>/`), donc des tags
pour le moteur de recherche. Plus la hiérarchie est fine, plus la recherche est
précise : chaque catégorie est donc détaillée en sous-catégories.

Le classement est heuristique : mots-clés du chemin et des topics du dépôt
(poids 3), puis description, imports et API reconnaissables dans le contenu
(poids 1, chaque motif compte une fois). Un fichier sans indice suffisant
va dans « non-classe » ; il peut ensuite être reclassé à la main (ou par
l'analyse IA facultative de l'API).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Dossier des fichiers sans indice suffisant. « divers » était le nom avant la 1.0.5 :
# il reste reconnu (voir LEGACY_UNCLASSIFIED) pour les bibliothèques existantes.
DEFAULT_CATEGORY = "non-classe"
LEGACY_UNCLASSIFIED = "divers"
UNCLASSIFIED = frozenset({DEFAULT_CATEGORY, LEGACY_UNCLASSIFIED})

MAX_TAGS = 10
MAX_TOPIC_TAGS = 4
MIN_SCORE = 2          # un seul indice faible (un mot de description) ne suffit pas

_Rule = tuple[tuple[str, ...], tuple[str, ...]]   # (mots-clés chemin/topics, motifs de contenu)

# catégorie -> (mots-clés, motifs, {sous-catégorie -> (mots-clés, motifs)})
TAXONOMY: dict[str, tuple[tuple[str, ...], tuple[str, ...], dict[str, _Rule]]] = {
    "api": (
        ("api", "rest", "server", "router", "routes", "endpoint", "graphql", "grpc", "http", "webhook"),
        (r"\bfastapi\b", r"\bflask\b", r"\bexpress\(", r"net/http", r"http\.server", r"\baxum\b",
         r"\bactix\b", r"@app\.(get|post|route)", r"\bhttp\.createServer\b", r"\bRestController\b"),
        {
            "rest": (("rest", "restful", "endpoint", "endpoints", "routes", "router", "controller", "handlers"),
                     (r"@app\.(get|post|put|delete|route)", r"\bRestController\b", r"\bGetMapping\b",
                      r"router\.(get|post|put|delete)\(", r"\bhttp\.HandleFunc\b", r"\bfastapi\b")),
            "graphql": (("graphql", "gql", "apollo", "resolvers"),
                        (r"\bgraphql\b", r"\btype Query\b", r"\bresolvers?\b")),
            "rpc": (("grpc", "rpc", "protobuf", "proto", "thrift", "jsonrpc"),
                    (r"\bgrpc\b", r"\bprotobuf\b", r"json-?rpc")),
            "temps-reel": (("websocket", "websockets", "socketio", "sse", "realtime", "stream"),
                           (r"\bwebsockets?\b", r"\bsocket\.io\b", r"\bEventSource\b")),
            "webhooks": (("webhook", "webhooks", "callback", "callbacks"),
                         (r"\bwebhooks?\b", r"X-Hub-Signature")),
            "authentification": (("auth", "oauth", "login", "jwt", "session", "sessions", "token", "tokens"),
                                 (r"\bjwt\b", r"\boauth2?\b", r"\bBearer\b", r"\bpassword_hash\b")),
            "clients-http": (("client", "clients", "sdk", "requests", "fetch", "scraper", "crawler"),
                             (r"\brequests\.(get|post)\(", r"\bhttpx\b", r"\baxios\b", r"\bfetch\(",
                              r"\breqwest\b", r"\bhttp\.Get\(")),
        },
    ),
    "algorithmes": (
        ("algorithm", "algorithms", "sort", "sorting", "graph", "tree", "search", "dynamic-programming",
         "leetcode", "data-structures", "structures"),
        (r"\bbinary.?search\b", r"\bdijkstra\b", r"\bquick.?sort\b", r"\bmerge.?sort\b",
         r"\blinked.?list\b", r"\bheap\b", r"\bbfs\b", r"\bdfs\b", r"\bmemoiz"),
        {
            "tri": (("sort", "sorting", "sorts", "quicksort", "mergesort", "heapsort"),
                    (r"\bquick.?sort\b", r"\bmerge.?sort\b", r"\bbubble.?sort\b", r"\bheap.?sort\b")),
            "recherche": (("search", "searching", "binary-search", "bisect", "lookup"),
                          (r"\bbinary.?search\b", r"\bbisect\b", r"\bkmp\b")),
            "graphes": (("graph", "graphs", "dijkstra", "bfs", "dfs", "pathfinding", "shortest-path"),
                        (r"\bdijkstra\b", r"\bbfs\b", r"\bdfs\b", r"\badjacency\b", r"\btopological\b")),
            "structures-de-donnees": (("tree", "trees", "heap", "stack", "queue", "linked-list", "trie",
                                       "data-structures", "structures"),
                                      (r"\blinked.?list\b", r"\bheap\b", r"\bbinary.?tree\b", r"\btrie\b",
                                       r"\bhash.?map\b")),
            "programmation-dynamique": (("dynamic-programming", "dp", "memoization", "knapsack"),
                                        (r"\bmemoiz", r"\bknapsack\b", r"\blru_cache\b")),
        },
    ),
    "fichiers-et-donnees": (
        ("data", "database", "db", "sql", "csv", "json", "parser", "parse", "etl", "storage", "file", "files", "io"),
        (r"\bsqlite3?\b", r"\bcsv\b", r"\bjson\.(load|dump)", r"\bBufReader\b", r"\bos\.ReadFile\b",
         r"\bSELECT\b.+\bFROM\b", r"\bCREATE TABLE\b", r"\bfs\.(readFile|writeFile)"),
        {
            "bases-de-donnees": (("database", "db", "sql", "sqlite", "postgres", "mysql", "orm", "migration"),
                                 (r"\bsqlite3?\b", r"\bSELECT\b.+\bFROM\b", r"\bCREATE TABLE\b",
                                  r"\bsqlalchemy\b", r"\bpsycopg", r"\bINSERT INTO\b")),
            "csv-et-tableurs": (("csv", "tsv", "excel", "xlsx", "spreadsheet", "dataframe"),
                                (r"\bcsv\b", r"\bopenpyxl\b", r"\bDictReader\b")),
            "json-et-serialisation": (("json", "serialization", "serializer", "marshal", "pickle", "msgpack"),
                                      (r"\bjson\.(load|dump)", r"\bJSON\.(parse|stringify)", r"\bserde\b",
                                       r"\bjson\.(Marshal|Unmarshal)")),
            "systeme-de-fichiers": (("file", "files", "filesystem", "fs", "path", "paths", "directory", "io"),
                                    (r"\bpathlib\b", r"\bos\.(walk|listdir)\b", r"\bBufReader\b",
                                     r"\bos\.ReadFile\b", r"\bfs\.(readFile|writeFile)")),
            "pipelines-etl": (("etl", "pipeline", "pipelines", "ingest", "transform", "batch"),
                              (r"\betl\b", r"\bpipeline\b", r"\bbatch\b")),
        },
    ),
    "web": (
        ("web", "frontend", "ui", "css", "html", "dom", "component", "components", "browser", "static"),
        (r"document\.(querySelector|getElementById)", r"addEventListener\(", r"<!doctype html",
         r"display:\s*(flex|grid)", r"\bReact\b", r"\bVue\b", r"@media"),
        {
            "dom-et-evenements": (("dom", "events", "browser", "vanilla", "javascript"),
                                  (r"document\.(querySelector|getElementById|createElement)",
                                   r"addEventListener\(", r"\bwindow\.")),
            "composants": (("component", "components", "react", "vue", "svelte", "angular", "widget"),
                           (r"\bReact\b", r"\bVue\b", r"\buseState\b", r"\bdefineComponent\b", r"\bsvelte\b")),
            "styles-css": (("css", "style", "styles", "stylesheet", "layout", "theme", "themes", "responsive"),
                           (r"display:\s*(flex|grid)", r"@media", r"@keyframes", r"--[a-z-]+:")),
            "pages-html": (("html", "template", "templates", "page", "pages", "landing"),
                           (r"<!doctype html", r"<html\b", r"<form\b")),
            "formulaires": (("form", "forms", "validation", "input"),
                            (r"<form\b", r"\bFormData\b", r"\bpreventDefault\b")),
        },
    ),
    "systeme": (
        ("system", "os", "cli", "shell", "process", "daemon", "kernel", "signal", "sysadmin", "devops", "tools"),
        (r"\bsubprocess\b", r"\bsigaction\b", r"\bfork\(", r"\bexecve?\b", r"std::process",
         r"\bargparse\b", r"\bos\.Args\b", r"#!/usr/bin/env (ba)?sh", r"\bsystemctl\b"),
        {
            "ligne-de-commande": (("cli", "command", "commands", "argparse", "arguments", "terminal", "console"),
                                  (r"\bargparse\b", r"\bclick\b", r"\bos\.Args\b", r"\bclap\b", r"\bcobra\b")),
            "processus": (("process", "processes", "daemon", "fork", "exec", "subprocess", "service"),
                          (r"\bsubprocess\b", r"\bfork\(", r"\bexecve?\b", r"std::process")),
            "signaux": (("signal", "signals", "interrupt", "sigterm"),
                        (r"\bsigaction\b", r"\bsignal\.signal\b", r"\bSIGTERM\b", r"\bSIGINT\b")),
            "scripts-shell": (("shell", "bash", "sh", "script", "scripts", "sysadmin"),
                              (r"#!/usr/bin/env (ba)?sh", r"#!/bin/(ba)?sh", r"\bsystemctl\b")),
            "monitoring": (("monitor", "monitoring", "metrics", "health", "uptime", "stats", "sysinfo"),
                           (r"\bpsutil\b", r"\bprometheus\b", r"/proc/")),
        },
    ),
    "concurrence": (
        ("async", "concurrency", "concurrent", "parallel", "thread", "threads", "worker", "actor", "queue"),
        (r"\basyncio\b", r"\bgo func\b", r"\bgoroutine\b", r"\btokio\b", r"std::thread",
         r"\bThreadPool", r"\bsync\.WaitGroup\b", r"\bPromise\.all\b", r"\bmpsc\b", r"\bcoroutine"),
        {
            "asynchrone": (("async", "asyncio", "await", "promise", "promises", "futures", "coroutine"),
                           (r"\basyncio\b", r"\bPromise\.all\b", r"\bcoroutine", r"\btokio\b", r"\basync def\b")),
            "threads": (("thread", "threads", "threading", "pool", "mutex", "lock", "locks"),
                        (r"\bThreadPool", r"std::thread", r"\bthreading\b", r"\bMutex\b")),
            "goroutines-et-canaux": (("goroutine", "goroutines", "channel", "channels", "csp"),
                                     (r"\bgo func\b", r"\bgoroutine\b", r"\bsync\.WaitGroup\b", r"\bmpsc\b")),
            "files-et-workers": (("queue", "queues", "worker", "workers", "job", "jobs", "scheduler", "tasks"),
                                 (r"\bqueue\.Queue\b", r"\bcelery\b", r"\bworker", r"\bscheduler\b")),
            "multiprocessing": (("multiprocessing", "parallel", "mapreduce", "cluster"),
                                (r"\bmultiprocessing\b", r"\brayon\b", r"\bProcessPool")),
        },
    ),
    "configuration": (
        ("config", "configuration", "settings", "docker", "deploy", "deployment", "ci", "env", "infra"),
        (r"\bdocker\b", r"(?m)^(?-i:FROM) \S+", r"\bapiVersion:", r"\bterraform\b", r"\[tool\.\w+\]",
         r"\bdotenv\b", r"\bos\.environ\b", r"\bprocess\.env\b"),
        {
            "conteneurs": (("docker", "dockerfile", "container", "containers", "compose", "podman", "kubernetes"),
                           (r"(?m)^(?-i:FROM) \S+", r"\bdocker\b", r"\bapiVersion:", r"\bkubectl\b")),
            "variables-d-environnement": (("env", "environment", "dotenv", "secrets"),
                                          (r"\bdotenv\b", r"\bos\.environ\b", r"\bprocess\.env\b")),
            "deploiement": (("deploy", "deployment", "release", "ci", "cd", "pipeline", "infra", "terraform"),
                            (r"\bterraform\b", r"\bansible\b", r"\bgithub actions\b")),
            "fichiers-de-configuration": (("config", "configuration", "settings", "toml", "yaml", "ini"),
                                          (r"\[tool\.\w+\]", r"\bConfigParser\b", r"\bsettings\b")),
        },
    ),
    "securite": (
        ("security", "crypto", "cryptography", "encryption", "hash", "hashing", "jwt", "oauth", "password",
         "ssl", "tls", "pentest", "sanitize"),
        (r"\bhashlib\b", r"\bhmac\b", r"\bbcrypt\b", r"\bcryptography\b", r"\bopenssl\b", r"\bAES\b",
         r"\bsha-?256\b", r"\bcrypto\.create"),
        {
            "chiffrement": (("crypto", "cryptography", "encryption", "encrypt", "decrypt", "aes", "rsa", "cipher"),
                            (r"\bAES\b", r"\bRSA\b", r"\bcryptography\b", r"\bFernet\b", r"\bcrypto\.create")),
            "hachage": (("hash", "hashing", "sha", "md5", "digest", "hmac", "checksum"),
                        (r"\bhashlib\b", r"\bhmac\b", r"\bsha-?(256|512)\b", r"\bbcrypt\b", r"\bargon2\b")),
            "certificats-et-tls": (("ssl", "tls", "certificate", "certificates", "x509", "pki"),
                                   (r"\bssl\b", r"\btls\b", r"\bx509\b", r"\bopenssl\b")),
            "validation-et-assainissement": (("sanitize", "validation", "escape", "xss", "injection", "csrf"),
                                             (r"\bsanitiz", r"\bescape\(", r"\bcsrf\b", r"\bxss\b")),
        },
    ),
    "reseau": (
        ("network", "networking", "socket", "sockets", "tcp", "udp", "dns", "proxy", "packet", "protocol", "ftp"),
        (r"\bsocket\.socket\b", r"\bnet\.Dial\b", r"\bTcpListener\b", r"\bUdpSocket\b", r"\bgetaddrinfo\b",
         r"\bscapy\b"),
        {
            "sockets": (("socket", "sockets", "tcp", "udp", "listener", "server"),
                        (r"\bsocket\.socket\b", r"\bnet\.Dial\b", r"\bTcpListener\b", r"\bUdpSocket\b")),
            "dns-et-adresses": (("dns", "ip", "ipv6", "cidr", "address", "resolver"),
                                (r"\bgetaddrinfo\b", r"\bipaddress\b", r"\bgethostbyname\b")),
            "proxy-et-tunnels": (("proxy", "tunnel", "vpn", "socks", "forward", "reverse"),
                                 (r"\bsocks5?\b", r"\bproxy\b", r"\btunnel\b")),
            "paquets": (("packet", "packets", "sniffer", "pcap", "scapy"),
                        (r"\bscapy\b", r"\bpcap\b", r"\bpacket\b")),
        },
    ),
    "texte-et-formats": (
        ("text", "regex", "regexp", "markdown", "xml", "yaml", "toml", "template", "templating", "tokenizer",
         "lexer", "parsing"),
        (r"\bre\.compile\b", r"\bregexp\b", r"\byaml\.safe_load\b", r"\bxml\.etree\b", r"\bjinja2?\b",
         r"\bHandlebars\b"),
        {
            "expressions-regulieres": (("regex", "regexp", "regular-expressions", "pattern", "patterns"),
                                       (r"\bre\.(compile|match|search|sub)\b", r"\bregexp\b", r"\bRegExp\(")),
            "analyse-syntaxique": (("lexer", "tokenizer", "parser", "ast", "grammar", "compiler", "interpreter"),
                                   (r"\blexer\b", r"\btokeniz", r"\bast\.parse\b", r"\bgrammar\b")),
            "markdown-et-xml": (("markdown", "xml", "html-parser", "rss", "svg"),
                                (r"\bxml\.etree\b", r"\bmarkdown\b", r"\blxml\b")),
            "gabarits": (("template", "templates", "templating", "jinja", "mustache"),
                         (r"\bjinja2?\b", r"\bHandlebars\b", r"\bmustache\b")),
            "yaml-et-toml": (("yaml", "yml", "toml", "ini"),
                             (r"\byaml\.safe_load\b", r"\btomllib\b", r"\btoml\b")),
        },
    ),
    "ia-et-calcul": (
        ("ml", "machine-learning", "ai", "neural", "numpy", "math", "matrix", "statistics", "scientific",
         "tensor", "data-science"),
        (r"\bnumpy\b", r"\btorch\b", r"\bsklearn\b", r"\btensorflow\b", r"\bpandas\b", r"\bnp\.array\b"),
        {
            "apprentissage-automatique": (("ml", "machine-learning", "neural", "training", "model", "models",
                                           "deep-learning", "classifier"),
                                          (r"\btorch\b", r"\bsklearn\b", r"\btensorflow\b", r"\bkeras\b")),
            "calcul-numerique": (("numpy", "numeric", "numerical", "matrix", "linear-algebra", "tensor"),
                                 (r"\bnumpy\b", r"\bnp\.array\b", r"\bndarray\b")),
            "statistiques": (("statistics", "stats", "probability", "regression", "distribution"),
                             (r"\bscipy\b", r"\bstatistics\b", r"\bmean\(")),
            "analyse-de-donnees": (("data-science", "analysis", "analytics", "pandas", "dataframe", "plot"),
                                   (r"\bpandas\b", r"\bmatplotlib\b", r"\bDataFrame\b")),
        },
    ),
    "interface-graphique": (
        ("gui", "tkinter", "qt", "gtk", "desktop", "widget", "widgets", "window", "swing"),
        (r"\btkinter\b", r"\bPyQt\d?\b", r"\bgtk\b", r"\bimgui\b", r"\bJFrame\b", r"\bwxPython\b"),
        {
            "tkinter-et-qt": (("tkinter", "tk", "qt", "pyqt", "pyside", "gtk", "wx"),
                              (r"\btkinter\b", r"\bPyQt\d?\b", r"\bPySide\d?\b", r"\bgtk\b")),
            "widgets": (("widget", "widgets", "button", "dialog", "window", "menu"),
                        (r"\bQWidget\b", r"\bJFrame\b", r"\bgtk_", r"\bDialog\b")),
            "terminal-ui": (("tui", "curses", "terminal", "ncurses", "ansi"),
                            (r"\bcurses\b", r"\bncurses\b", r"\bratatui\b", r"\\x1b\[")),
        },
    ),
    "jeux": (
        ("game", "games", "pygame", "sprite", "opengl", "vulkan", "physics", "gamedev"),
        (r"\bpygame\b", r"\bglfw\b", r"\bopengl\b", r"\bsprite\b", r"\bVector2\b"),
        {
            "moteur-et-rendu": (("engine", "render", "rendering", "opengl", "vulkan", "shader", "graphics"),
                                (r"\bopengl\b", r"\bglfw\b", r"\bvulkan\b", r"\bshader\b")),
            "physique": (("physics", "collision", "collisions", "rigidbody", "simulation"),
                         (r"\bcollision\b", r"\brigid.?body\b", r"\bvelocity\b")),
            "logique-de-jeu": (("game", "games", "pygame", "sprite", "level", "player", "board"),
                               (r"\bpygame\b", r"\bsprite\b", r"\bgame.?loop\b")),
        },
    ),
    "outils-dev": (
        ("logging", "log", "debug", "lint", "utils", "helpers", "automation", "packaging"),
        (r"\blogging\.getLogger\b", r"\bimport logging\b", r"\bsetuptools\b", r"\bpytest\b", r"\bunittest\b"),
        {
            "journalisation": (("logging", "log", "logs", "logger", "debug", "trace", "tracing"),
                               (r"\blogging\.getLogger\b", r"\bimport logging\b", r"\btracing::", r"\blog\.")),
            "outils-de-test": (("testing", "mock", "mocks", "fixture", "fixtures", "assert"),
                               (r"\bpytest\b", r"\bunittest\b", r"\bmock\b")),
            "utilitaires": (("utils", "util", "helpers", "helper", "common", "misc"),
                            (r"\bdef (slugify|retry|chunked)\b",)),
            "packaging": (("packaging", "setup", "build", "release", "publish", "bundle"),
                          (r"\bsetuptools\b", r"\bpyproject\b", r"\bwheel\b")),
            "automatisation": (("automation", "automate", "bot", "cron", "schedule", "scheduler", "script"),
                               (r"\bcron\b", r"\bschedule\b", r"\bautomat")),
        },
    ),
}

# Compatibilité : anciennes vues (catégorie -> (mots-clés, motifs)).
RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    name: (keywords, patterns) for name, (keywords, patterns, _subs) in TAXONOMY.items()
}

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_FOLDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,39}$")
_TOPIC_SLUG = re.compile(r"[^a-z0-9._+-]+")

_COMPILED = {name: [re.compile(p, re.IGNORECASE) for p in patterns] for name, (_, patterns) in RULES.items()}
_COMPILED_SUBS = {
    (category, sub): [re.compile(p, re.IGNORECASE) for p in patterns]
    for category, (_k, _p, subs) in TAXONOMY.items()
    for sub, (_kw, patterns) in subs.items()
}

CATEGORY_LABELS = {DEFAULT_CATEGORY: "Non classé", LEGACY_UNCLASSIFIED: "Non classé"}


@dataclass(frozen=True)
class Classification:
    """Résultat détaillé : le dossier est `category[/subcategory]`."""

    category: str
    subcategory: str = ""
    tags: tuple[str, ...] = ()
    score: int = 0

    @property
    def folder_parts(self) -> tuple[str, ...]:
        return (self.category, self.subcategory) if self.subcategory else (self.category,)


def is_valid_folder_name(name: str) -> bool:
    """Un nom de dossier de catégorie : minuscules, chiffres et `._+-`, jamais caché ni relatif."""
    return bool(_FOLDER_NAME.fullmatch(name)) and ".." not in name and not name.startswith(".")


def slugify_part(text: str) -> str | None:
    """Rend un libellé libre utilisable comme nom de dossier (ou None s'il ne reste rien)."""
    folded = (text or "").strip().lower()
    for src, dst in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("â", "a"), ("ç", "c"),
                     ("ù", "u"), ("û", "u"), ("ô", "o"), ("î", "i"), ("ï", "i")):
        folded = folded.replace(src, dst)
    folded = _TOPIC_SLUG.sub("-", folded).strip("-._")
    folded = re.sub(r"\.{2,}", ".", folded)[:40].strip("-._")
    return folded if folded and is_valid_folder_name(folded) else None


def _score(keywords: tuple[str, ...], words: set[str], description_words: set[str],
           patterns: list[re.Pattern], sample: str) -> tuple[int, list[str]]:
    hits = [keyword for keyword in keywords if keyword in words]
    score = 3 * len(hits)
    score += sum(1 for keyword in keywords if keyword in description_words)
    score += sum(1 for pattern in patterns if pattern.search(sample))
    return score, hits


def classify_detailed(path: str, topics: list[str] | None = None, description: str = "",
                      content: str = "") -> Classification:
    """Catégorie, sous-catégorie et tags les plus probables (`non-classe` sans indice suffisant)."""
    words = set(_WORD_SPLIT.split(path.lower()))
    topic_slugs: list[str] = []
    for topic in topics or []:
        words.update(_WORD_SPLIT.split(topic.lower()))
        words.add(topic.lower())
        slug = slugify_part(topic)
        if slug and slug not in topic_slugs:
            topic_slugs.append(slug)
    # Les mots de la description sont un indice faible (poids 1, comme le contenu).
    description_words = set(_WORD_SPLIT.split(description.lower())) - words

    sample = content[:20_000]
    best_name, best_score, best_hits = DEFAULT_CATEGORY, 0, []

    for name, (keywords, _patterns, _subs) in TAXONOMY.items():
        score, hits = _score(keywords, words, description_words, _COMPILED[name], sample)
        if score > best_score:
            best_name, best_score, best_hits = name, score, hits

    if best_score < MIN_SCORE:
        return Classification(DEFAULT_CATEGORY, "", tuple(topic_slugs[:MAX_TOPIC_TAGS]), 0)

    # Sous-catégorie : même barème, uniquement parmi celles de la catégorie retenue.
    best_sub, best_sub_score, sub_hits = "", 0, []
    for sub, (keywords, _patterns) in TAXONOMY[best_name][2].items():
        score, hits = _score(keywords, words, description_words, _COMPILED_SUBS[(best_name, sub)], sample)
        if score > best_sub_score:
            best_sub, best_sub_score, sub_hits = sub, score, hits
    if best_sub_score < MIN_SCORE:
        best_sub, sub_hits = "", []

    tags: list[str] = [best_name]
    for tag in [best_sub, *sub_hits, *best_hits, *topic_slugs[:MAX_TOPIC_TAGS]]:
        if tag and tag not in tags:
            tags.append(tag)
    return Classification(best_name, best_sub, tuple(tags[:MAX_TAGS]), best_score)


def classify(path: str, topics: list[str] | None = None, description: str = "", content: str = "") -> str:
    """Nom de la catégorie la plus probable (`non-classe` par défaut)."""
    return classify_detailed(path, topics, description, content).category
