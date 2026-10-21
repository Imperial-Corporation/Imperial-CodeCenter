#!/usr/bin/env python3
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.
"""Mesure les performances d'une instance sur CETTE machine (bibliothèque standard uniquement).

    python3 scripts/bench.py --url http://127.0.0.1:8000 --token icc_pat_…  [-n 40] [--queries mots.txt]

Ce qui est mesuré :
  * recherche à froid (première fois) puis à chaud (cache) : médiane, p95, maximum ;
  * état de l'indexation et ressources du serveur, si le jeton ou la session le permet
    (--admin-token = CODECENTER_ADMIN_TOKEN) ;
  * un verdict simple et le profil conseillé (CODECENTER_PROFILE).

Le jeton d'API se crée dans l'interface (Compte → Jetons d'API).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_QUERIES = ["json", "read file", "http server", "sort list", "parse date", "database connection",
                   "async", "regex", "thread", "error handling"]


def call(url: str, token: str | None, timeout: float = 60.0):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "icc-bench"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read())
    return (time.perf_counter() - started) * 1000.0, data


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))]


def describe(label: str, values: list[float]) -> str:
    return (f"{label:<22} médiane {statistics.median(values):7.1f} ms · p95 {percentile(values, 95):7.1f} ms · "
            f"max {max(values):7.1f} ms  ({len(values)} requêtes)")


def verdict(cold_p95: float) -> tuple[str, str]:
    if cold_p95 < 150:
        return "très rapide", "standard (rien à changer)"
    if cold_p95 < 600:
        return "correct", "standard ou lite"
    if cold_p95 < 2000:
        return "lent (machine modeste ou disque dur)", "lite : CODECENTER_PROFILE=lite (docs/performance.md)"
    return "très lent", "minimal : CODECENTER_PROFILE=minimal, et vérifie que l'index de contenu est actif (docs/performance.md)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", help="jeton d'API (icc_pat_…)")
    parser.add_argument("--admin-token", help="CODECENTER_ADMIN_TOKEN, pour l'état du serveur")
    parser.add_argument("-n", type=int, default=30, help="nombre de recherches à froid")
    parser.add_argument("--queries", help="fichier de requêtes, une par ligne")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")

    queries = DEFAULT_QUERIES
    if args.queries:
        with open(args.queries, encoding="utf-8") as handle:
            queries = [line.strip() for line in handle if line.strip()] or DEFAULT_QUERIES

    try:
        _, about = call(f"{base}/api/v1/about", None, 10)
        print(f"Instance : {about.get('name')} {about.get('version')} — {base}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Instance injoignable : {exc}", file=sys.stderr)
        return 1

    def search(query: str) -> float:
        params = urllib.parse.urlencode({"q": query, "limit": args.limit})
        elapsed, _ = call(f"{base}/api/v1/code/search?{params}", args.token)
        return elapsed

    try:
        cold = [search(f"{queries[i % len(queries)]} {i}") for i in range(max(args.n, 3))]  # chaque requête est inédite
        warm = [search(f"{queries[0]} 0") for _ in range(max(args.n, 3))]  # même requête : cache
    except urllib.error.HTTPError as exc:
        hint = " (jeton d'API absent ou invalide : --token)" if exc.code == 401 else ""
        print(f"Erreur HTTP {exc.code}{hint}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(describe("Recherche à froid", cold))
    print(describe("Recherche à chaud (cache)", warm))

    if args.admin_token:
        try:
            _, index = call(f"{base}/api/v1/admin/index", args.admin_token)
            scan = index.get("scan", {})
            print(f"Index : {index.get('stats', {}).get('documents_total', '?')} documents ; dernier passage "
                  f"{scan.get('last_duration_secs', '?')} s ({scan.get('seen', '?')} fichiers vus)")
            _, system = call(f"{base}/api/v1/admin/system", args.admin_token)
            proc, machine = system.get("process", {}), system.get("machine", {})
            print(f"Mémoire du processus : {proc.get('rss', 0) / 1024 ** 2:.0f} Mio ; "
                  f"machine : {machine.get('cpus')} cœur(s), profil {system.get('profile', {}).get('profile')}")
        except (urllib.error.URLError, OSError, ValueError):
            print("(état du serveur indisponible : jeton d'administration refusé)")

    label, advice = verdict(percentile(cold, 95))
    print(f"\nVerdict : {label}. Profil conseillé : {advice}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
