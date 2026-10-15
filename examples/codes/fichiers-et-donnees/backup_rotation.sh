#!/usr/bin/env bash
# Sauvegarde un dossier en archive datée et ne garde que les N plus récentes.
# Usage : backup_rotation.sh <source> <destination> [nombre_a_garder]
set -euo pipefail

source_dir="${1:?dossier source manquant}"
backup_dir="${2:?dossier de destination manquant}"
keep="${3:-7}"

mkdir -p "$backup_dir"
archive="$backup_dir/$(basename "$source_dir")-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$archive" -C "$(dirname "$source_dir")" "$(basename "$source_dir")"
echo "Archive créée : $archive"

# Supprime les plus anciennes au-delà de $keep.
ls -1t "$backup_dir"/*.tar.gz | tail -n +"$((keep + 1))" | while read -r old; do
    rm -- "$old"
    echo "Supprimée : $old"
done
