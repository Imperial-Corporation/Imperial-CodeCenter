#!/usr/bin/env python3
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Ajoute (ou vérifie) l'en-tête d'origine et de licence dans les fichiers source.

    python3 scripts/add_license_headers.py            # ajoute les en-têtes manquants
    python3 scripts/add_license_headers.py --check    # échoue s'il en manque (CI)

Idempotent : un fichier déjà marqué n'est pas modifié. Ignore les dossiers
générés (.venv, target, node_modules, __pycache__) et examples/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "Imperial Community License"
LINES = (
    "Imperial CodeCenter — https://imperial-corporation.pages.dev/icc",
    "Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.",
)
STYLES = {  # extension -> (préfixe de ligne, ouverture, fermeture)
    ".py": ("# ", None, None), ".sh": ("# ", None, None), ".toml": ("# ", None, None),
    ".rs": ("// ", None, None), ".js": ("// ", None, None),
    ".css": (None, "/*", " */"),
}
SKIP_DIRS = {".venv", "target", "node_modules", "__pycache__", ".git", ".local", "examples", "images", "assets"}


def header(ext: str) -> str:
    prefix, opening, closing = STYLES[ext]
    if prefix:
        return "".join(f"{prefix}{line}\n" for line in LINES)
    return opening + "\n" + "".join(f" * {line}\n" for line in LINES) + closing + "\n"


def candidates() -> list[Path]:
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in STYLES:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return found


def process(path: Path, write: bool) -> bool:
    """Retourne True si le fichier est (ou devient) conforme sans modification."""
    text = path.read_text(encoding="utf-8")
    if MARKER in "\n".join(text.splitlines()[:8]):
        return True
    if write:
        lines = text.splitlines(keepends=True)
        keep = 0
        # Garde la ligne shebang et la déclaration d'encodage en tête.
        while keep < min(2, len(lines)) and (lines[keep].startswith("#!") or "coding" in lines[keep][:20]):
            keep += 1
        head = "".join(lines[:keep])
        rest = "".join(lines[keep:])
        path.write_text(head + header(path.suffix) + ("\n" if rest and not rest.startswith("\n") else "") + rest,
                        encoding="utf-8")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="ne modifie rien ; code de sortie 1 s'il manque un en-tête")
    args = parser.parse_args()
    missing = [p for p in candidates() if not process(p, write=not args.check)]
    for path in missing:
        print(("manquant : " if args.check else "ajouté : ") + str(path.relative_to(ROOT)))
    if args.check and missing:
        return 1
    print(f"{len(candidates())} fichiers vérifiés, {len(missing)} {'manquants' if args.check else 'modifiés'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
