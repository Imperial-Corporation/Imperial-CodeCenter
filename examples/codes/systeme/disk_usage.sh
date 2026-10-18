#!/usr/bin/env bash
# Alerte si une partition dépasse un seuil d'occupation (par défaut 90 %).
set -euo pipefail

threshold="${1:-90}"
status=0

# df -P : format POSIX stable ; on saute la ligne d'en-tête.
while read -r _ _ _ _ usage mount; do
    percent="${usage%\%}"
    if [[ "$percent" =~ ^[0-9]+$ ]] && (( percent >= threshold )); then
        echo "ALERTE : $mount utilisé à ${percent} %"
        status=1
    fi
done < <(df -P | tail -n +2)

(( status == 0 )) && echo "Occupation disque sous ${threshold} %"
exit "$status"
