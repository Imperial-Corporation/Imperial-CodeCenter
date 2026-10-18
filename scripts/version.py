#!/usr/bin/env python3
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.
"""Cohérence des versions du projet.

    python3 scripts/version.py check            # toutes les versions sont identiques ?
    python3 scripts/version.py check --tag v1.0.3   # ... et égales à celle du tag Git
    python3 scripts/version.py set 1.0.4        # met à jour tous les fichiers
    python3 scripts/version.py notes 1.0.3      # extrait la section du CHANGELOG (notes de version)

Code de sortie : 0 = ok, 1 = incohérence ou section introuvable.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# (fichier, motif avec UN groupe capturant la version). Le premier résultat est utilisé.
TARGETS: list[tuple[str, str]] = [
    ("VERSION", r"^(\d+\.\d+\.\d+)\s*$"),
    ("engine/Cargo.toml", r'^version = "(\d+\.\d+\.\d+)"'),
    ("engine/Cargo.lock", r'name = "codecenter-engine"\nversion = "(\d+\.\d+\.\d+)"'),
    ("engine/pyproject.toml", r'^version = "(\d+\.\d+\.\d+)"'),
    ("api/pyproject.toml", r'^version = "(\d+\.\d+\.\d+)"'),
    ("crawler/pyproject.toml", r'^version = "(\d+\.\d+\.\d+)"'),
    ("api/src/icc_ai/about.py", r'^VERSION = "(\d+\.\d+\.\d+)"'),
    ("crawler/src/icc_crawler/__init__.py", r'^__version__ = "(\d+\.\d+\.\d+)"'),
    ("README.md", r"\*\*Version (\d+\.\d+\.\d+)\*\*"),
    ("docs/README.md", r"— v(\d+\.\d+\.\d+)"),
    ("docs/api.md", r"\(v(\d+\.\d+\.\d+)\)"),
    ("CHANGELOG.md", r"^## (\d+\.\d+\.\d+)\s*$"),
]


def _search(path: str, pattern: str) -> tuple[str, re.Match[str] | None]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return text, re.search(pattern, text, re.MULTILINE)


def current_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for path, pattern in TARGETS:
        _, match = _search(path, pattern)
        result[path] = match.group(1) if match else None
    return result


def check(tag: str | None) -> int:
    versions = current_versions()
    reference = versions["VERSION"]
    bad = {path: found for path, found in versions.items() if found != reference}
    for path, found in versions.items():
        print(f"{'OK ' if path not in bad else 'KO '} {path}: {found}")
    if bad:
        print(f"\nVersions différentes de VERSION ({reference}) : {', '.join(bad)}", file=sys.stderr)
        return 1
    if tag is not None:
        wanted = tag[1:] if tag.startswith("v") else tag
        if wanted != reference:
            print(f"\nLe tag {tag} ne correspond pas à VERSION ({reference}).", file=sys.stderr)
            return 1
    return 0


def set_version(new: str) -> int:
    if not SEMVER.fullmatch(new):
        print("Version attendue : X.Y.Z", file=sys.stderr)
        return 1
    for path, pattern in TARGETS:
        if path == "CHANGELOG.md":
            continue  # la section se rédige à la main
        text, match = _search(path, pattern)
        if not match:
            print(f"Motif introuvable dans {path}", file=sys.stderr)
            return 1
        start, end = match.span(1)
        (ROOT / path).write_text(text[:start] + new + text[end:], encoding="utf-8")
    print(f"Version {new} appliquée. Pense à ajouter la section « ## {new} » au CHANGELOG.")
    return 0


def notes(version: str) -> int:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match or not match.group(1).strip():
        print(f"Aucune section « ## {version} » dans CHANGELOG.md", file=sys.stderr)
        return 1
    print(match.group(1).strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--tag")
    sub.add_parser("set").add_argument("version")
    sub.add_parser("notes").add_argument("version")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.tag)
    if args.command == "set":
        return set_version(args.version)
    return notes(args.version)


if __name__ == "__main__":
    sys.exit(main())
