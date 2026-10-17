#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Met à jour les plages d'adresses de Cloudflare depuis la source officielle
# (https://www.cloudflare.com/ips/) et régénère la configuration nginx si le
# proxy est installé.
#
# Usage : update_cloudflare_ips.sh [--check]
#   --check : affiche les plages sans rien modifier.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/config/proxy/cloudflare-ips.txt"
V4="https://www.cloudflare.com/ips-v4"
V6="https://www.cloudflare.com/ips-v6"
die() { echo "Erreur : $*" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || die "curl est requis."

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
{ curl -fsS --max-time 20 "$V4"; echo; curl -fsS --max-time 20 "$V6"; } | sed '/^$/d' > "$TMP" ||
    die "Téléchargement impossible."

# Garde-fou : uniquement des CIDR valides, et un minimum de plages plausible.
while IFS= read -r line; do
    [[ "$line" =~ ^[0-9a-fA-F:.]+/[0-9]{1,3}$ ]] || die "Ligne inattendue : $line"
done < "$TMP"
(( $(wc -l < "$TMP") >= 10 )) || die "Liste suspecte (trop courte) : abandon."

if [[ "${1:-}" == "--check" ]]; then cat "$TMP"; exit 0; fi

mkdir -p "$(dirname "$OUT")"
if [[ -f "$OUT" ]] && diff -q "$OUT" "$TMP" >/dev/null; then
    echo "Plages Cloudflare déjà à jour ($(wc -l < "$OUT") plages)."
else
    install -m 644 "$TMP" "$OUT"
    echo "Plages Cloudflare mises à jour : $OUT ($(wc -l < "$OUT") plages)."
    echo "Pour les appliquer : ./scripts/setup_proxy.sh enable ... (relance la génération nginx)"
fi
