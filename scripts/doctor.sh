#!/usr/bin/env bash
# Diagnostic complet d'une installation : permissions, indexation, base, proxy.
# Remplace l'audit manuel (ls, find -readable, journalctl…) par un seul rapport.
#
# Usage : ./scripts/doctor.sh            (lecture seule, ne modifie rien)
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
[[ -f "$ROOT/config/deploy.conf" ]] && source "$ROOT/config/deploy.conf"
SERVICE_NAME="${SERVICE_NAME:-imperial-codecenter}"
DATA_DIR="${DATA_DIR:-/var/lib/imperial-codecenter}"
API_HOST="${API_HOST:-${HOST:-127.0.0.1}}"
API_PORT="${API_PORT:-${PORT:-8000}}"
DB_NAME="${DB_NAME:-imperial_codecenter}"
WATCH="$DATA_DIR/codes"
ENV_FILE="/etc/${SERVICE_NAME}/.env"
SUDO=""; ((EUID == 0)) || SUDO="sudo"

OK=0; WARN=0; BAD=0
ok()   { echo "  [OK]    $*"; OK=$((OK+1)); }
warn() { echo "  [ATTN]  $*"; WARN=$((WARN+1)); }
bad()  { echo "  [ERREUR] $*"; BAD=$((BAD+1)); }
title() { echo; echo "== $* =="; }

title "Service"
if $SUDO systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then ok "service $SERVICE_NAME actif"; else bad "service $SERVICE_NAME inactif (sudo journalctl -u $SERVICE_NAME -n 80)"; fi
if id "$SERVICE_NAME" >/dev/null 2>&1; then ok "compte de service : $(id "$SERVICE_NAME")"; else bad "compte de service absent"; fi

title "Dossier surveillé ($WATCH)"
if [[ -d "$WATCH" ]]; then
    ls -ld "$WATCH" | sed 's/^/  /'
    if $SUDO runuser -u "$SERVICE_NAME" -- test -w "$WATCH" 2>/dev/null; then ok "le service peut écrire à la racine"; else bad "le service NE peut PAS écrire à la racine : ./scripts/fix_permissions.sh"; fi
    UNWRITABLE="$($SUDO find "$WATCH" -type d 2>/dev/null | while read -r d; do $SUDO runuser -u "$SERVICE_NAME" -- test -w "$d" 2>/dev/null || echo "$d"; done | head -20)"
    if [[ -n "$UNWRITABLE" ]]; then
        warn "dossiers non inscriptibles par le service (les fichiers y sont indexés SANS rangement) :"
        echo "$UNWRITABLE" | sed 's/^/          /'
        echo "          -> ./scripts/fix_permissions.sh"
    else ok "tous les dossiers sont inscriptibles par le service"; fi
    UNREADABLE="$($SUDO find "$WATCH" -type f 2>/dev/null | while read -r f; do $SUDO runuser -u "$SERVICE_NAME" -- test -r "$f" 2>/dev/null || echo "$f"; done | head -20)"
    if [[ -n "$UNREADABLE" ]]; then
        bad "fichiers ILLISIBLES par le service (jamais indexés) :"; echo "$UNREADABLE" | sed 's/^/          /'
        echo "          -> ./scripts/fix_permissions.sh"
    else ok "tous les fichiers sont lisibles par le service"; fi
    if [[ -d "$WATCH/_depot" ]]; then ok "zone de dépôt présente : $WATCH/_depot ($(find "$WATCH/_depot" -type f ! -name 'LISEZ-MOI.txt' 2>/dev/null | wc -l) fichier(s) en attente)"; else warn "zone de dépôt absente (relance release.sh)"; fi
    ok "$(find "$WATCH" -type f ! -path '*/_depot/*' ! -path '*/.crawler/*' ! -name '.codecenter-language-dir' 2>/dev/null | wc -l) fichier(s) dans la bibliothèque"
else
    bad "dossier introuvable"
fi

title "Journaux récents"
LOGS="$($SUDO journalctl -u "$SERVICE_NAME" --since '-24 hours' --no-pager 2>/dev/null)"
PERM="$(echo "$LOGS" | grep -ci 'permission denied')"
IDX="$(echo "$LOGS" | grep -c "impossible d'indexer")"
if ((PERM > 0)); then bad "$PERM ligne(s) 'Permission denied' ces dernières 24 h :"; echo "$LOGS" | grep -i 'permission denied' | tail -5 | sed 's/^/          /'; else ok "aucun 'Permission denied' ces dernières 24 h"; fi
((IDX > 0)) && warn "$IDX échec(s) d'indexation ces dernières 24 h" || ok "aucun échec d'indexation"

title "Base PostgreSQL"
if command -v psql >/dev/null 2>&1; then
    COUNT="$($SUDO -u "$SERVICE_NAME" psql --dbname="$DB_NAME" --tuples-only --no-align --command='SELECT COUNT(*) FROM code_examples;' 2>/dev/null || true)"
    if [[ -n "$COUNT" ]]; then
        ok "$COUNT document(s) indexé(s)"
        echo "  (le statut d'analyse « pending » est normal : il n'a aucun effet sur la recherche)"
    else bad "requête impossible (PostgreSQL arrêté ou droits ?)"; fi
else warn "psql absent"; fi

title "API"
URL="http://${API_HOST}:${API_PORT}"
if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "$URL/api/health" >/dev/null 2>&1; then ok "API joignable ($URL)"; else bad "API injoignable ($URL)"; fi
    TOKEN="$($SUDO grep -s '^CODECENTER_ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"')"
    HEALTH="$(curl -fsS --max-time 8 ${TOKEN:+-H "Authorization: Bearer $TOKEN"} "$URL/api/v1/index/health" 2>/dev/null || true)"
    if [[ -n "$HEALTH" ]]; then
        HEALTH_JSON="$HEALTH" python3 - <<'PY'
import json, os
d = json.loads(os.environ["HEALTH_JSON"])
failures = d.get("failures", [])
print(f"  fichiers en échec d'indexation : {len(failures)}")
for item in failures[:10]:
    print(f"    - {item['path']} : {item['error']} ({item['attempts']} essai(s))")
read_only = d.get("read_only_directories", [])
print(f"  dossiers indexés sans rangement (non inscriptibles) : {len(read_only)}")
for item in read_only[:10]:
    print("    -", item)
print(f"  fichiers ignorés (trop gros…) : {len(d.get('skipped', []))}")
PY
    else warn "rapport de santé de l'index indisponible (jeton d'administration ?)"; fi
    curl -fsS --max-time 5 "$URL/api/v1/proxy/check" 2>/dev/null | sed 's/^/  proxy : /' && echo
fi

title "Disque"
df -h "$DATA_DIR" 2>/dev/null | tail -1 | awk '{print "  " $4 " libres sur " $2 " (" $5 " utilisés)"}'
FREE_KB="$(df -Pk "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $4}')"
if [[ -n "$FREE_KB" ]] && ((FREE_KB < 2*1024*1024)); then warn "moins de 2 Go libres : le crawler s'arrêtera de lui-même (garde disque)"; fi

echo
echo "Bilan : $OK ok, $WARN attention, $BAD erreur(s)."
((BAD == 0))
