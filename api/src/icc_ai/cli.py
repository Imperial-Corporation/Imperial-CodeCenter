# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""`icc` : ligne de commande et serveur MCP (stdio) d'une instance CodeCenter.

Uniquement la bibliothèque standard : fonctionne sur n'importe quelle machine, sans installer l'API.

Connexion : jeton d'API créé dans l'interface (Compte → Jetons d'API).

    export ICC_URL=https://code.exemple.fr        # défaut : http://127.0.0.1:8000
    export ICC_TOKEN=icc_pat_…                    # ou : icc config --url … --token …

    icc status
    icc search "lire un fichier json" [-l python] [-n 5] [--json]
    icc show <id> [--start 10 --end 40]
    icc filters
    icc mcp                      # serveur MCP (stdio) pour Claude Desktop, Claude Code, etc.

Sauvegardes (sur la machine qui héberge l'instance, voir `python -m icc_ai.backup`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000"
TIMEOUT = 30
MAX_BODY = 8 * 1024 * 1024


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "icc" / "config.json"


def load_config() -> dict:
    cfg: dict = {}
    try:
        cfg = json.loads(config_path().read_text())
    except (OSError, ValueError):
        pass
    return {
        "url": (os.environ.get("ICC_URL") or cfg.get("url") or DEFAULT_URL).rstrip("/"),
        "token": os.environ.get("ICC_TOKEN") or cfg.get("token") or "",
    }


def save_config(url: str | None, token: str | None) -> Path:
    path = config_path()
    current: dict = {}
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    if url:
        current["url"] = url.rstrip("/")
    if token:
        current["token"] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(current, handle, indent=1)
    os.chmod(path, 0o600)
    return path


class ClientError(Exception):
    pass


class Client:
    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    def get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
        request = urllib.request.Request(f"{self.url}{path}" + (f"?{query}" if query else ""))
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "icc-cli")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read(MAX_BODY))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read(65536)).get("detail", "")
            except Exception:
                pass
            if exc.code == 401:
                raise ClientError("Non autorisé : jeton d'API absent, révoqué ou expiré (ICC_TOKEN).") from None
            raise ClientError(f"Erreur {exc.code} : {detail or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ClientError(f"Instance injoignable ({self.url}) : {getattr(exc, 'reason', exc)}") from None
        except ValueError:
            raise ClientError("Réponse illisible de l'instance.") from None

    def search(self, query: str, limit: int = 10, language: str | None = None, category: str | None = None):
        return self.get("/api/v1/code/search", {"q": query, "limit": limit, "language": language, "category": category})

    def document(self, document_id: str, start: int | None = None, end: int | None = None):
        return self.get(f"/api/v1/code/documents/{urllib.parse.quote(document_id, safe='')}",
                        {"start_line": start, "end_line": end})

    def filters(self):
        return self.get("/api/v1/code/filters")


# --------------------------------------------------------------------------
# Texte pour humains
# --------------------------------------------------------------------------

def format_results(data: dict) -> str:
    results = data.get("results", [])
    if not results:
        return "Aucun résultat."
    lines = []
    for i, hit in enumerate(results, 1):
        lines.append(f"{i}. {hit.get('relative_path')}  [{hit.get('language_name') or hit.get('language')}]"
                     f"  score {hit.get('score')}  id {hit.get('document_id')}")
        snippet = (hit.get("snippet") or "").rstrip()
        if snippet:
            lines.append(f"   lignes {hit.get('line_start')}-{hit.get('line_end')}")
            lines.extend("   │ " + row for row in snippet.splitlines()[:12])
    return "\n".join(lines)


def format_document(doc: dict) -> str:
    return doc.get("content", "")


# --------------------------------------------------------------------------
# Serveur MCP (JSON-RPC 2.0 sur stdio, un message par ligne)
# --------------------------------------------------------------------------

MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

MCP_TOOLS = [
    {
        "name": "search_code",
        "description": "Cherche dans la bibliothèque de code indexée d'Imperial CodeCenter (mots-clés). "
                       "Renvoie chemins, langage, score, id de document et extraits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Mots-clés ou description."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                "language": {"type": "string", "description": "Filtre de langage (ex. python)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_code",
        "description": "Lit un document trouvé par search_code (id de document), éventuellement une plage de lignes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_filters",
        "description": "Langages et catégories disponibles pour filtrer la recherche.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def mcp_call(client: Client, name: str, arguments: dict) -> dict:
    try:
        if name == "search_code":
            query = str(arguments.get("query", "")).strip()
            if not query:
                return _text_result("Requête vide.", True)
            limit = max(1, min(int(arguments.get("limit", 8) or 8), 20))
            return _text_result(format_results(client.search(query, limit, arguments.get("language") or None)))
        if name == "read_code":
            doc = client.document(str(arguments.get("document_id", "")), arguments.get("start_line"),
                                  arguments.get("end_line"))
            return _text_result(format_document(doc))
        if name == "list_filters":
            data = client.filters()
            langs = ", ".join(l.get("slug", "") for l in data.get("languages", []))
            cats = ", ".join(c.get("slug", "") for c in data.get("categories", [])[:60])
            return _text_result(f"Langages : {langs}\nCatégories : {cats}")
    except ClientError as exc:
        return _text_result(str(exc), True)
    except (TypeError, ValueError) as exc:
        return _text_result(f"Arguments invalides : {exc}", True)
    return _text_result(f"Outil inconnu : {name}", True)


def mcp_handle(client: Client, message: dict) -> dict | None:
    """Traite un message JSON-RPC ; renvoie la réponse (None pour une notification)."""
    method, ident = message.get("method"), message.get("id")
    params = message.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def fail(code, text):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}

    if ident is None:  # notification (initialized, cancelled…) : pas de réponse
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({
            "protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "imperial-codecenter", "version": _version()},
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        return ok(mcp_call(client, str(params.get("name", "")), params.get("arguments") or {}))
    return fail(-32601, f"Méthode inconnue : {method}")


def serve_mcp(client: Client, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}}
        else:
            reply = mcp_handle(client, message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def _version() -> str:
    try:
        from .about import VERSION

        return VERSION
    except Exception:
        return "1.0.4"


# --------------------------------------------------------------------------
# Ligne de commande
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icc", description="Client en ligne de commande d'Imperial CodeCenter.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cfg = sub.add_parser("config", help="enregistre l'URL et le jeton (~/.config/icc/config.json, 0600)")
    cfg.add_argument("--url")
    cfg.add_argument("--token")
    sub.add_parser("status", help="vérifie la connexion")
    search = sub.add_parser("search", help="recherche dans le code indexé")
    search.add_argument("query", nargs="+")
    search.add_argument("-l", "--language")
    search.add_argument("-c", "--category")
    search.add_argument("-n", "--limit", type=int, default=10)
    search.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="affiche un document")
    show.add_argument("id")
    show.add_argument("--start", type=int)
    show.add_argument("--end", type=int)
    sub.add_parser("filters", help="langages et catégories")
    sub.add_parser("mcp", help="serveur MCP (stdio)")
    args = parser.parse_args(argv)

    if args.cmd == "config":
        if not (args.url or args.token):
            print("Rien à enregistrer : --url et/ou --token.", file=sys.stderr)
            return 2
        print(f"Configuration enregistrée : {save_config(args.url, args.token)}")
        return 0

    settings = load_config()
    client = Client(settings["url"], settings["token"])
    try:
        if args.cmd == "status":
            about = client.get("/api/v1/about")
            print(f"{about.get('name', 'CodeCenter')} {about.get('version')} — {client.url}")
            who = "jeton configuré" if client.token else "pas de jeton (ICC_TOKEN)"
            print(who)
        elif args.cmd == "search":
            data = client.search(" ".join(args.query), max(1, min(args.limit, 50)), args.language, args.category)
            print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else format_results(data))
        elif args.cmd == "show":
            print(format_document(client.document(args.id, args.start, args.end)))
        elif args.cmd == "filters":
            data = client.filters()
            print("Langages  :", ", ".join(l["slug"] for l in data.get("languages", [])))
            print("Catégories:", ", ".join(c["slug"] for c in data.get("categories", [])))
        elif args.cmd == "mcp":
            serve_mcp(client)
    except ClientError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
