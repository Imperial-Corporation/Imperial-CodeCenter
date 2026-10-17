# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Ligne de commande : `icc-crawler crawl|stats|export-manifest|disk-check`."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

import httpx

from . import __version__
from .crawler import GIB, Budget, CrawlConfig, Crawler, Stats, _existing_ancestor
from .filters import DEFAULT_LICENSES, GITHUB_LANGUAGE_NAMES, FilterConfig
from .github import GitHubClient
from .storage import Store

# Codes de sortie : 0 = exécution voulue (fin normale, budget ou arrêt demandé) ; 1 = problème ;
# 2 = utilisation incorrecte.
EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2
PROBLEM_REASONS = {"limite-de-debit", "erreur", "disque-presque-plein"}

PROGRESS_EVERY_SECONDS = 2.0
PROGRESS_EVERY_FILES = 5


# -- validation des arguments ---------------------------------------------------------


def _number(text: str, kind: type) -> float:
    try:
        value = kind(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"nombre invalide : {text!r}") from None
    if isinstance(value, float) and not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"valeur non finie : {text!r}")
    return value


def positive_float(text: str) -> float:
    value = _number(text, float)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"doit être strictement positif (reçu : {text})")
    return value


def positive_int(text: str) -> int:
    value = _number(text, int)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"doit être un entier strictement positif (reçu : {text})")
    return value


def non_negative_int(text: str) -> int:
    value = _number(text, int)
    if value < 0:
        raise argparse.ArgumentTypeError(f"ne peut pas être négatif (reçu : {text})")
    return value


def non_negative_float(text: str) -> float:
    value = _number(text, float)
    if value < 0:
        raise argparse.ArgumentTypeError(f"ne peut pas être négatif (reçu : {text})")
    return value


def iso_datetime(text: str) -> datetime:
    """ISO 8601 ; sans fuseau, l'heure est interprétée comme heure locale."""
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"date/heure ISO 8601 invalide : {text!r} (exemple : 2026-10-12T06:30 ou 2026-10-12T06:30:00+02:00)"
        ) from None
    return value.astimezone() if value.tzinfo is None else value


def repo_name(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text) or ".." in text.split("/"):
        raise argparse.ArgumentTypeError(f"dépôt invalide : {text!r} (attendu : propriétaire/nom)")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icc-crawler",
        description="Collecte des scripts sous licence permissive depuis GitHub et les range "
                    "automatiquement dans le dossier surveillé d'Imperial CodeCenter.",
        epilog="Basé sur Imperial CodeCenter, créé par Imperial — https://imperial-corporation.pages.dev/icc "
               "· Imperial Community License 1.0",
    )
    parser.add_argument("--version", action="version", version=f"icc-crawler {__version__} — Imperial CodeCenter, par Imperial (https://imperial-corporation.pages.dev/icc)")
    parser.add_argument("-v", "--verbose", action="store_true", help="journal détaillé (sur stderr)")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="explorer GitHub et enregistrer des fichiers")
    crawl.add_argument("--dest", required=True, type=Path,
                       help="dossier surveillé (CODECENTER_WATCH_PATHS) où ranger les fichiers")
    crawl.add_argument("--language", action="append", default=[], choices=sorted(GITHUB_LANGUAGE_NAMES),
                       help="langage à collecter (répétable)")
    crawl.add_argument("--query", default="", help="mots-clés de recherche de dépôts")
    crawl.add_argument("--topic", action="append", default=[], help="topic GitHub (répétable)")
    crawl.add_argument("--repo", action="append", default=[], metavar="OWNER/NOM", type=repo_name,
                       help="dépôt précis à explorer (répétable)")
    crawl.add_argument("--min-stars", type=non_negative_int, default=100)
    crawl.add_argument("--max-repos", type=positive_int, default=20)
    crawl.add_argument("--max-files-per-repo", type=positive_int, default=15)
    crawl.add_argument("--max-files", type=positive_int, default=200, help="plafond total de fichiers enregistrés")
    crawl.add_argument("--max-kb", type=positive_int, default=60, help="taille maximale d'un fichier (Ko)")
    crawl.add_argument("--license", action="append", dest="licenses", metavar="SPDX",
                       help=f"licence acceptée (répétable). Défaut : {', '.join(sorted(DEFAULT_LICENSES))}")
    crawl.add_argument("--include-tests", action="store_true", help="garder aussi les fichiers de test")
    crawl.add_argument("--pause", type=non_negative_float, default=0.2, help="pause entre requêtes (s)")
    crawl.add_argument("--dry-run", action="store_true", help="afficher sans rien écrire")

    budget = crawl.add_argument_group("budgets d'arrêt (l'exécution s'arrête proprement et peut être reprise)")
    budget.add_argument("--stop-at", type=iso_datetime, metavar="ISO8601",
                        help="heure absolue d'arrêt (sans fuseau : heure locale)")
    budget.add_argument("--max-minutes", type=positive_float, metavar="N", help="durée maximale (minutes)")
    budget.add_argument("--max-gb", type=positive_float, metavar="X",
                        help="octets maximum écrits par cette exécution (Go = 1024^3 octets)")
    budget.add_argument("--max-library-gb", type=positive_float, metavar="X",
                        help="taille maximale du dossier destination, fichiers existants inclus (hors .crawler/)")
    budget.add_argument("--min-free-gb", type=non_negative_float, default=2.0, metavar="G",
                        help="espace disque libre à préserver (défaut : 2.0 ; 0 désactive la garde)")
    budget.add_argument("--progress-json", action="store_true",
                        help="une ligne JSON par événement sur stdout (start, progress, done) ; "
                             "les messages vont sur stderr")

    stats = sub.add_parser("stats", help="résumé de ce qui a été collecté")
    stats.add_argument("--dest", required=True, type=Path)

    export = sub.add_parser("export-manifest", help="exporter le manifeste de provenance (JSON Lines)")
    export.add_argument("--dest", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)

    disk = sub.add_parser("disk-check", help="vérifier l'espace libre du disque de destination")
    disk.add_argument("--dest", required=True, type=Path)
    disk.add_argument("--min-free-gb", type=non_negative_float, default=2.0, metavar="G")
    return parser


# -- sortie JSON ------------------------------------------------------------------------


class ProgressReporter:
    """Écrit les événements JSON (une ligne chacun) ; `progress` est limité en fréquence."""

    def __init__(self, stream: TextIO, redact: Callable[[str], str] = lambda t: t,
                 monotonic: Callable[[], float] = time.monotonic):
        self._stream = stream
        self._redact = redact
        self._monotonic = monotonic
        self._last_time = monotonic()
        self._last_files = 0

    def emit(self, payload: dict) -> None:
        line = self._redact(json.dumps(payload, ensure_ascii=False))
        self._stream.write(line + "\n")
        self._stream.flush()

    def start(self, info: dict) -> None:
        self._last_time = self._monotonic()
        self.emit({"event": "start", **info})

    def progress(self, stats: Stats) -> None:
        now = self._monotonic()
        if (stats.files_saved - self._last_files < PROGRESS_EVERY_FILES
                and now - self._last_time < PROGRESS_EVERY_SECONDS):
            return
        self._last_time = now
        self._last_files = stats.files_saved
        self.emit({
            "event": "progress", "files": stats.files_saved, "bytes": stats.bytes_written,
            "repos": stats.repos_seen, "skipped": stats.skipped_total,
            "elapsed": round(stats.elapsed_seconds, 1),
        })

    def done(self, stats: Stats) -> None:
        self.emit({"event": "done", "stop_reason": stats.stop_reason, "stats": stats.as_dict()})


# -- crawl ------------------------------------------------------------------------------


def _install_signal_handlers(crawler_stop: threading.Event) -> Callable[[], None]:
    """SIGTERM/SIGINT : arrêt gracieux ; un second SIGINT interrompt brutalement. Retourne la restauration."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def handler(signum, _frame):
        if crawler_stop.is_set() and signum == signal.SIGINT:
            raise KeyboardInterrupt
        print(f"Signal {signal.Signals(signum).name} reçu : arrêt gracieux en cours…", file=sys.stderr)
        crawler_stop.set()

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, handler)

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore


def _run_crawl(args: argparse.Namespace, transport: httpx.BaseTransport | None = None) -> int:
    if not (args.language or args.query or args.topic or args.repo):
        print("Précise au moins --language, --query, --topic ou --repo.", file=sys.stderr)
        return EXIT_USAGE

    clock_now = datetime.now(timezone.utc)
    if args.stop_at is not None and args.stop_at <= clock_now:
        print(f"--stop-at est déjà dépassé ({args.stop_at.isoformat()}).", file=sys.stderr)
        return EXIT_USAGE

    token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    if token is None:
        print("Attention : GITHUB_TOKEN absent. Limite de 60 requêtes/h, la recherche sera très limitée.",
              file=sys.stderr)

    filters = FilterConfig(
        licenses=frozenset(args.licenses) if args.licenses else DEFAULT_LICENSES,
        max_bytes=args.max_kb * 1024,
        include_tests=args.include_tests,
        languages=frozenset(args.language) if args.language else None,
    )
    config = CrawlConfig(
        destination=args.dest, languages=args.language, query=args.query, topics=args.topic,
        repos=args.repo, min_stars=args.min_stars, max_repos=args.max_repos,
        max_files_per_repo=args.max_files_per_repo, max_files=args.max_files,
        dry_run=args.dry_run, filters=filters,
    )
    budget = Budget(
        stop_at=args.stop_at,
        max_seconds=args.max_minutes * 60 if args.max_minutes else None,
        max_bytes=int(args.max_gb * GIB) if args.max_gb else None,
        max_library_bytes=int(args.max_library_gb * GIB) if args.max_library_gb else None,
        min_free_bytes=int(args.min_free_gb * GIB),
    )

    stop = threading.Event()
    # Les attentes (pause, quota) se réveillent dès qu'un arrêt est demandé.
    client = GitHubClient(token, transport=transport, pause=args.pause,
                          sleep=lambda seconds: stop.wait(max(0.0, seconds)) and None)
    json_mode = args.progress_json
    reporter = ProgressReporter(sys.stdout, client.redact) if json_mode else None
    human = sys.stderr if json_mode else sys.stdout

    try:
        store = None if args.dry_run else Store(args.dest)
    except OSError as exc:
        client.close()
        print(f"Impossible de préparer le dossier de destination : {client.redact(str(exc))}", file=sys.stderr)
        return EXIT_PROBLEM

    crawler = Crawler(config, client, store, budget=budget, stop_event=stop,
                      on_progress=reporter.progress if reporter else None,
                      echo=lambda text: print(text, file=human))
    restore = _install_signal_handlers(stop)
    if reporter:
        reporter.start({
            "dest": str(args.dest), "languages": list(args.language), "dry_run": bool(args.dry_run),
            "authenticated": client.authenticated, "budget": budget.describe(),
        })
    try:
        stats = crawler.run()
    finally:
        restore()
        client.close()
        if store:
            store.close()

    if reporter:
        reporter.done(stats)

    print(f"\nDépôts examinés : {stats.repos_seen} (déjà à jour : {stats.repos_skipped})", file=human)
    print(f"Fichiers {'qui seraient ' if args.dry_run else ''}enregistrés : {stats.files_saved} "
          f"({stats.bytes_written // 1024} Ko écrits, {stats.elapsed_seconds:.0f} s)", file=human)
    if stats.files_skipped:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(stats.files_skipped.items()))
        print(f"Fichiers écartés : {detail}", file=human)
    for error in stats.errors[:10]:
        print(f"Avertissement : {client.redact(error)}", file=sys.stderr)
    print(f"Fin : {stats.stop_reason}", file=human if stats.stop_reason == "termine" else sys.stderr)
    return EXIT_PROBLEM if stats.stop_reason in PROBLEM_REASONS else EXIT_OK


def _run_disk_check(args: argparse.Namespace) -> int:
    usage = shutil.disk_usage(_existing_ancestor(args.dest))
    minimum = int(args.min_free_gb * GIB)
    enough = usage.free >= minimum
    print(f"Espace libre : {usage.free / GIB:.2f} Go sur {usage.total / GIB:.2f} Go "
          f"(seuil : {minimum / GIB:.2f} Go) -> {'OK' if enough else 'INSUFFISANT'}")
    return EXIT_OK if enough else EXIT_PROBLEM


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:      # argparse : --help/--version (0) ou erreur d'usage (2)
        return exc.code if isinstance(exc.code, int) else (EXIT_OK if exc.code is None else EXIT_USAGE)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s", stream=sys.stderr)

    if args.command == "crawl":
        return _run_crawl(args, transport)
    if args.command == "disk-check":
        return _run_disk_check(args)

    if not args.dest.is_dir():
        print(f"Dossier introuvable : {args.dest}", file=sys.stderr)
        return EXIT_PROBLEM
    store = Store(args.dest)
    try:
        if args.command == "stats":
            data = store.stats()
            print(f"{data['files']} fichiers, {data['repos']} dépôts, {data['bytes'] // 1024} Ko")
            print("Catégories :", ", ".join(f"{k}={v}" for k, v in data["by_category"].items()) or "aucune")
            print("Langages   :", ", ".join(f"{k}={v}" for k, v in data["by_language"].items()) or "aucun")
        else:
            print(f"{store.export_manifest(args.output)} entrées écrites dans {args.output}")
    finally:
        store.close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
