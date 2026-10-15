#!/usr/bin/env bash
# Vérifie qu'une URL répond avec un code 2xx, avec nouvelles tentatives.
# Usage : healthcheck.sh http://127.0.0.1:8000/api/health [essais] [pause_s]
set -u

url="${1:?URL manquante}"
tries="${2:-10}"
pause="${3:-1}"

for attempt in $(seq 1 "$tries"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" || true)"
    if [[ "$code" =~ ^2 ]]; then
        echo "OK ($code) après $attempt essai(s)"
        exit 0
    fi
    echo "Essai $attempt/$tries : code ${code:-aucun}"
    sleep "$pause"
done

echo "Échec : $url ne répond pas" >&2
exit 1
