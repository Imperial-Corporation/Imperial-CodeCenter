#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/config/deploy.conf"

PURGE_DATA=0

usage() {
    cat <<'EOF'
Usage : ./scripts/clean.sh [--purge-data]

Options :
  --purge-data   Supprime aussi le répertoire de données persistant.
                 Cette action est irréversible.
  -h, --help     Affiche cette aide.

Le nettoyage ne supprime pas la base PostgreSQL ni son rôle.
EOF
}

die() {
    echo "Erreur : $*" >&2
    exit 1
}

while (($# > 0)); do
    case "$1" in
        --purge-data)
            PURGE_DATA=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "Option inconnue : $1"
            ;;
    esac
    shift
done

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo."
command -v sudo >/dev/null 2>&1 || die "sudo est requis."
command -v cargo >/dev/null 2>&1 || die "cargo est requis."

[[ -f "$CONFIG" ]] || die "config/deploy.conf introuvable."

# shellcheck source=/dev/null
source "$CONFIG"

[[ "${SERVICE_NAME:-}" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "SERVICE_NAME absent ou invalide."

[[ "${INSTALL_DIR:-}" == /* ]] ||
    die "INSTALL_DIR doit être un chemin absolu."

[[ "${DATA_DIR:-}" == /* ]] ||
    die "DATA_DIR doit être un chemin absolu."

[[ "$INSTALL_DIR" != "/" && "$DATA_DIR" != "/" ]] ||
    die "La suppression d'un répertoire racine est interdite."

[[ "$INSTALL_DIR" != "$ROOT" ]] ||
    die "INSTALL_DIR pointe sur le dépôt source."

[[ "$INSTALL_DIR" != "$DATA_DIR" ]] ||
    die "INSTALL_DIR et DATA_DIR doivent être distincts."

for path in "$INSTALL_DIR" "$DATA_DIR"; do
    [[ "$path" != *"/../"* && "$path" != */.. ]] ||
        die "Chemin contenant un segment parent interdit : $path"
done

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_DIR="/etc/${SERVICE_NAME}"
STAGING_DIR="${INSTALL_DIR}.staging"
BACKUP_DIR="${INSTALL_DIR}.previous"

echo "=== Imperial CodeCenter : nettoyage ==="
echo "Service : $SERVICE_NAME"
echo "Installation : $INSTALL_DIR"
echo "Données : $DATA_DIR"

if ((PURGE_DATA)); then
    echo
    echo "ATTENTION : --purge-data supprimera définitivement :"
    echo "  $DATA_DIR"
    read -r -p "Pour confirmer, tape SUPPRIMER : " confirmation
    [[ "$confirmation" == "SUPPRIMER" ]] ||
        die "Suppression des données annulée."
fi

echo "==> [1/4] Arrêt et désactivation du service..."

if sudo systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
    sudo systemctl stop "$SERVICE_NAME" || true
    sudo systemctl disable "$SERVICE_NAME" || true
fi

echo "==> [2/4] Suppression de l'installation..."

sudo rm -f -- "$SERVICE_FILE"
sudo rm -rf -- "$INSTALL_DIR" "$STAGING_DIR" "$BACKUP_DIR"
sudo rm -rf -- "$ENV_DIR"

sudo systemctl daemon-reload
sudo systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

if ((PURGE_DATA)); then
    echo "==> Suppression des données persistantes..."
    sudo rm -rf -- "$DATA_DIR"
else
    echo "==> Données persistantes conservées."
fi

echo "==> [3/4] Nettoyage des caches Python locaux..."

# Supprimer les répertoires de cache sans parcourir leur contenu.
find "$ROOT" -depth -type d \( \
    -name __pycache__ -o \
    -name .pytest_cache -o \
    -name .mypy_cache -o \
    -name .ruff_cache \
\) -exec rm -rf -- {} +

# Supprimer les fichiers Python compilés restants.
find "$ROOT" -type f -name '*.pyc' -exec rm -f -- {} +


echo "==> [4/4] Nettoyage du cache de compilation Rust..."

(
    cd "$ROOT/engine"
    cargo clean
)

echo
echo "Nettoyage terminé."
echo "Le déploiement systemd a été retiré s'il existait."
echo "La base PostgreSQL et son rôle n'ont pas été supprimés."
if ((PURGE_DATA)); then
    echo "Les données locales ont également été supprimées."
fi