#!/usr/bin/env bash
# Génère un Makefile minimal (cibles build, test, clean) puis l'exécute.
set -euo pipefail

dir="$(mktemp -d)"
trap 'rm -rf "$dir"' EXIT

# Les lignes de recette d'un Makefile doivent commencer par une tabulation.
printf '%s\n' \
    '.PHONY: build test clean' \
    'build:' \
    '	@echo "compilation..."' \
    'test: build' \
    '	@echo "tests ok"' \
    'clean:' \
    '	@echo "nettoyage"' > "$dir/Makefile"

if command -v make >/dev/null 2>&1; then
    make -C "$dir" test
else
    echo "make n'est pas installé" >&2
    exit 1
fi
