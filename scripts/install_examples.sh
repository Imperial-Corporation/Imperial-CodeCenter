#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Copie les scripts d'exemple (examples/codes) dans le dossier surveillé.
#
# Usage : install_examples.sh <dossier_surveillé> [--force]
#
# Par défaut, l'installation n'a lieu qu'une fois : un fichier marqueur
# (.examples-installed) évite de recréer les exemples que tu as supprimés
# à la mise à jour suivante. --force les réinstalle (sans écraser tes fichiers).
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/examples/codes"
TARGET="${1:?Usage : install_examples.sh <dossier_surveillé> [--force]}"
FORCE="${2:-}"
MARKER="$TARGET/.examples-installed"

[[ -d "$SOURCE" ]] || { echo "Aucun dossier d'exemples ($SOURCE)." >&2; exit 0; }
mkdir -p "$TARGET"

if [[ -f "$MARKER" && "$FORCE" != "--force" ]]; then
    echo "Exemples déjà installés (utilise --force pour les réinstaller)."
    exit 0
fi

# N'écrase jamais un fichier existant (copie fichier par fichier, portable).
while IFS= read -r -d '' file; do
    relative="${file#"$SOURCE"/}"
    if [[ ! -e "$TARGET/$relative" ]]; then
        mkdir -p "$(dirname "$TARGET/$relative")"
        cp -- "$file" "$TARGET/$relative"
    fi
done < <(find "$SOURCE" -type f -print0)
date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER"

count="$(find "$SOURCE" -type f | wc -l)"
echo "Exemples installés : $count fichier(s) dans $TARGET"
