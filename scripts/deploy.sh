#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/config/deploy.conf"

die() {
    echo "Erreur : $*" >&2
    exit 1
}

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo."
command -v sudo >/dev/null 2>&1 || die "sudo est requis."
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
    die "Un répertoire racine ne peut pas être utilisé."

[[ "$INSTALL_DIR" != "$ROOT" ]] ||
    die "INSTALL_DIR ne doit pas pointer vers le dépôt source."

[[ "$INSTALL_DIR" != "$DATA_DIR" ]] ||
    die "INSTALL_DIR et DATA_DIR doivent être distincts."

DB_NAME="${DB_NAME:-imperial_codecenter}"
DB_USER="${DB_USER:-$SERVICE_NAME}"

[[ "$DB_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "DB_NAME invalide."

[[ "$DB_USER" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "DB_USER invalide."

echo "=== Imperial CodeCenter : préparation du serveur ==="

echo "==> [1/6] Installation des dépendances système..."

sudo apt-get update
sudo apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    git \
    libssl-dev \
    pkg-config \
    postgresql \
    postgresql-client \
    python3 \
    python3-dev \
    python3-venv

echo "==> [2/6] Installation de Rust (rustup, rustc, cargo) si nécessaire..."

# shellcheck source=/dev/null
source "$ROOT/scripts/lib/toolchain.sh"
ensure_rust

echo "==> [3/6] Installation de uv si nécessaire..."

if ! command -v uv >/dev/null 2>&1; then
    if [[ -x "$HOME/.local/bin/uv" ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi
fi

command -v uv >/dev/null 2>&1 ||
    die "Installation de uv échouée."

echo "==> [4/6] Préparation du compte de service..."

if ! id "$SERVICE_NAME" >/dev/null 2>&1; then
    sudo useradd \
        --system \
        --home-dir "$DATA_DIR" \
        --no-create-home \
        --shell /usr/sbin/nologin \
        "$SERVICE_NAME"
fi

echo "==> [5/6] Préparation des répertoires..."

sudo install -d -o "$SERVICE_NAME" -g "$SERVICE_NAME" -m 750 \
    "$INSTALL_DIR" \
    "$DATA_DIR" \
    "$DATA_DIR/codes" \
    "$DATA_DIR/imports" \
    "$DATA_DIR/cache" \
    "$DATA_DIR/tmp"

sudo install -d -o root -g root -m 755 \
    "$(dirname "$INSTALL_DIR")"

echo "==> [6/6] Préparation de PostgreSQL..."

sudo systemctl enable --now postgresql

sudo -u postgres psql \
    --set=ON_ERROR_STOP=1 \
    --set=db_user="$DB_USER" \
    --set=db_name="$DB_NAME" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'db_user')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'db_user'
)
\gexec

SELECT format(
    'CREATE DATABASE %I OWNER %I',
    :'db_name',
    :'db_user'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'db_name'
)
\gexec
SQL

echo
echo "Préparation terminée."
echo "Compte de service : $SERVICE_NAME"
echo "Répertoire d'installation : $INSTALL_DIR"
echo "Répertoire de données : $DATA_DIR"
echo "Base PostgreSQL : $DB_NAME"
echo "Rôle PostgreSQL : $DB_USER"
echo
echo "Prochaine étape : configurer config/.env puis lancer release.sh."