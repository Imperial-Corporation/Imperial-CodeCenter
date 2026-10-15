#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/config/deploy.conf"
ENV_SOURCE="$ROOT/config/.env"

die() {
    echo "Erreur : $*" >&2
    exit 1
}

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo."
command -v sudo >/dev/null 2>&1 || die "sudo est requis."
command -v cargo >/dev/null 2>&1 || die "cargo est introuvable."
command -v python3 >/dev/null 2>&1 || die "python3 est introuvable."
command -v psql >/dev/null 2>&1 || die "psql est introuvable."
[[ -f "$CONFIG" ]] || die "config/deploy.conf introuvable."
[[ -f "$ENV_SOURCE" ]] || die "config/.env introuvable."
[[ -f "$ROOT/engine/Cargo.toml" ]] || die "Cargo.toml introuvable."
[[ -f "$ROOT/api/pyproject.toml" ]] || die "api/pyproject.toml introuvable."
[[ -f "$ROOT/api/src/main.py" ]] || die "api/src/main.py introuvable."

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
    die "INSTALL_DIR pointe vers le dépôt source."

[[ "$INSTALL_DIR" != "$DATA_DIR" ]] ||
    die "INSTALL_DIR et DATA_DIR doivent être distincts."

DB_NAME="${DB_NAME:-imperial_codecenter}"
DB_USER="${DB_USER:-$SERVICE_NAME}"
API_HOST="${API_HOST:-${HOST:-127.0.0.1}}"
API_PORT="${API_PORT:-${PORT:-8000}}"

[[ "$DB_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "DB_NAME invalide."

[[ "$DB_USER" == "$SERVICE_NAME" ]] ||
    die "Pour l'authentification PostgreSQL par socket, DB_USER doit être égal à SERVICE_NAME."

[[ "$API_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]] ||
    die "API_HOST invalide."

[[ "$API_PORT" =~ ^[0-9]+$ ]] &&
    ((API_PORT >= 1 && API_PORT <= 65535)) ||
    die "API_PORT invalide."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_DIR="/etc/${SERVICE_NAME}"
ENV_DEST="$ENV_DIR/.env"
STAGING_DIR="${INSTALL_DIR}.staging"
BACKUP_DIR="${INSTALL_DIR}.previous"
BUILD_DIR="$(mktemp -d)"
WHEEL_DIR="$BUILD_DIR/wheels"
WATCH_PATH="$DATA_DIR/codes"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" && -x "$HOME/.local/bin/uv" ]]; then
    UV_BIN="$HOME/.local/bin/uv"
fi
[[ -n "$UV_BIN" ]] || die "uv est introuvable. Lance d'abord deploy.sh."

cleanup() {
    rm -rf -- "$BUILD_DIR"
}
trap cleanup EXIT

echo "=== Imperial CodeCenter : release ==="
echo "Installation : $INSTALL_DIR"
echo "Données : $DATA_DIR"
echo "Watcher : $WATCH_PATH"

echo "==> [1/8] Vérification du compte de service..."

id "$SERVICE_NAME" >/dev/null 2>&1 ||
    die "Compte de service absent. Lance d'abord deploy.sh."

sudo systemctl cat "$SERVICE_NAME" >/dev/null 2>&1 &&
    sudo systemctl stop "$SERVICE_NAME"

echo "==> [2/8] Préparation de la configuration..."

sudo install -d -o root -g root -m 700 "$ENV_DIR"

# Clé de chiffrement des secrets IA déjà installée : elle doit survivre aux
# mises à jour, sinon les clés API enregistrées deviendraient illisibles.
EXISTING_SECRET="$(sudo grep -s '^CODECENTER_SECRET_KEY=' "$ENV_DEST" | head -n1 || true)"

python3 - "$ENV_SOURCE" "$BUILD_DIR/.env" "$WATCH_PATH" "$DB_NAME" "$EXISTING_SECRET" <<'PY'
import base64
import os
import sys
from pathlib import Path

source, destination, watch_path, db_name, existing_secret = sys.argv[1:]
lines = Path(source).read_text(encoding="utf-8").splitlines()

def set_value(key: str, value: str) -> None:
    prefix = f"{key}="
    matches = [
        i for i, line in enumerate(lines)
        if line.strip().startswith(prefix)
    ]

    replacement = f'{key}="{value}"'

    if matches:
        lines[matches[0]] = replacement
        for i in reversed(matches[1:]):
            del lines[i]
    else:
        lines.append(replacement)

set_value("CODECENTER_WATCH_PATHS", watch_path)

# Clé Fernet (32 octets en base64 URL-safe) : conservée si déjà définie dans
# config/.env ou déjà installée, générée sinon. Elle reste dans /etc (root,
# 0600), séparée des données chiffrées stockées dans DATA_DIR.
if not any(line.strip().startswith("CODECENTER_SECRET_KEY=") for line in lines):
    if existing_secret:
        lines.append(existing_secret.strip())
    else:
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        set_value("CODECENTER_SECRET_KEY", key)

# L'API utilise l'identité Unix du service pour PostgreSQL
# via le socket local, sans mot de passe dans l'URL.
if not any(line.strip().startswith("DATABASE_URL=") for line in lines):
    set_value(
        "DATABASE_URL",
        f"postgresql:///{db_name}?host=/var/run/postgresql",
    )

Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

sudo install -d -o "$SERVICE_NAME" -g "$SERVICE_NAME" -m 750 \
    "$WATCH_PATH" \
    "$DATA_DIR/imports" \
    "$DATA_DIR/cache" \
    "$DATA_DIR/tmp"

# Scripts d'exemple (une seule fois ; voir scripts/install_examples.sh).
sudo "$ROOT/scripts/install_examples.sh" "$WATCH_PATH"

# Zone de dépôt (voir docs/depot.md) : on y copie des scripts, le service les range.
sudo install -d -o "$SERVICE_NAME" -g "$SERVICE_NAME" -m 2775 "$WATCH_PATH/_depot"

# Permissions : groupe du service + setgid + ACL par défaut, SANS chown/chmod
# généralisés (voir scripts/fix_permissions.sh et docs/permissions.md). Le
# service peut ainsi ranger même des fichiers déposés par un autre compte.
"$ROOT/scripts/fix_permissions.sh" "$WATCH_PATH" "$SERVICE_NAME" --user "$USER"

sudo install -o root -g root -m 600 "$BUILD_DIR/.env" "$ENV_DEST"

echo "==> [3/8] Construction du moteur Rust..."

mkdir -p "$WHEEL_DIR"

(
    cd "$ROOT/engine"
    "$UV_BIN" run --with maturin maturin build \
        --release \
        --manifest-path "$ROOT/engine/Cargo.toml" \
        --out "$WHEEL_DIR"
)

WHEEL="$(find "$WHEEL_DIR" -maxdepth 1 -type f -name 'codecenter_engine-*.whl' -print -quit)"
[[ -n "$WHEEL" ]] || die "Aucun wheel Rust n'a été généré."

echo "==> [4/8] Préparation de la nouvelle version..."

sudo rm -rf -- "$STAGING_DIR"
sudo mkdir -p "$STAGING_DIR"
sudo chown "$USER":"$(id -gn)" "$STAGING_DIR"

cp -a "$ROOT/api" "$STAGING_DIR/api"

if [[ -d "$ROOT/frontend" ]]; then
    cp -a "$ROOT/frontend" "$STAGING_DIR/frontend"
fi

# Le crawler GitHub (paquet autonome) : l'API le lance dans un sous-processus.
if [[ -d "$ROOT/crawler/src" ]]; then
    mkdir -p "$STAGING_DIR/crawler"
    cp -a "$ROOT/crawler/src" "$STAGING_DIR/crawler/src"
    cp -a "$ROOT/crawler/pyproject.toml" "$STAGING_DIR/crawler/pyproject.toml"
fi

if [[ -d "$ROOT/config/proxy" ]]; then
    mkdir -p "$STAGING_DIR/config"
    cp -a "$ROOT/config/proxy" "$STAGING_DIR/config/proxy"
fi

if [[ -d "$ROOT/docs" ]]; then
    cp -a "$ROOT/docs" "$STAGING_DIR/docs"
fi

if [[ -d "$ROOT/migrations" ]]; then
    cp -a "$ROOT/migrations" "$STAGING_DIR/migrations"
fi

if [[ -d "$ROOT/engine/migrations" ]]; then
    cp -a "$ROOT/engine/migrations" "$STAGING_DIR/engine-migrations"
fi

API_DIR="$STAGING_DIR/api"

# Crée un environnement de production indépendant du dossier personnel.
rm -rf -- "$API_DIR/.venv"

"$UV_BIN" sync \
    --project "$API_DIR" \
    --python /usr/bin/python3

PYTHON="$API_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || die "Environnement Python de production absent."

"$UV_BIN" pip install --python "$PYTHON" "$WHEEL"

# Vérifie que le moteur Rust et Uvicorn sont importables.
"$PYTHON" -c "import uvicorn; import codecenter_engine; print('Environnement de production valide.')"

PYTHON="$API_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || die "Environnement Python de production absent."

sudo chown -R "$SERVICE_NAME:$SERVICE_NAME" "$STAGING_DIR"
sudo chmod -R u+rwX,go+rX "$STAGING_DIR"

echo "==> [5/8] Vérification de PostgreSQL..."

sudo systemctl is-active --quiet postgresql ||
    die "PostgreSQL n'est pas actif."

sudo -u "$SERVICE_NAME" psql \
    --dbname="$DB_NAME" \
    --set=ON_ERROR_STOP=1 \
    --command='SELECT 1;' >/dev/null ||
    die "Connexion PostgreSQL impossible pour le compte de service."

echo "==> [6/8] Application des migrations disponibles..."

MIGRATION_DIRS=()
[[ -d "$ROOT/config/database/migrations" ]] &&
    MIGRATION_DIRS+=("$ROOT/config/database/migrations")
[[ -d "$ROOT/engine/src/database/migrations" ]] &&
    MIGRATION_DIRS+=("$ROOT/engine/src/database/migrations")
[[ -d "$ROOT/migrations" ]] &&
    MIGRATION_DIRS+=("$ROOT/migrations")

# Les fichiers SQL sont appliqués une seule fois, par ordre alphabétique.
if ((${#MIGRATION_DIRS[@]} > 0)); then
    sudo -u "$SERVICE_NAME" psql \
        --dbname="$DB_NAME" \
        --set=ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS codecenter_schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

    for directory in "${MIGRATION_DIRS[@]}"; do
        while IFS= read -r -d '' migration; do
            filename="$(basename "$migration")"

            already_applied="$(
                sudo -u "$SERVICE_NAME" psql \
                    --dbname="$DB_NAME" \
                    --tuples-only --no-align \
                    --command="SELECT 1 FROM codecenter_schema_migrations WHERE filename = '$(
                        printf '%s' "$filename" | sed "s/'/''/g"
                    )';"
            )"

            [[ "$already_applied" == "1" ]] && continue

            echo "Migration : $filename"

            sudo -u "$SERVICE_NAME" psql \
                --dbname="$DB_NAME" \
                --set=ON_ERROR_STOP=1 \
                --single-transaction \
                --file="$migration"

            escaped_filename="$(printf '%s' "$filename" | sed "s/'/''/g")"

            sudo -u "$SERVICE_NAME" psql \
                --dbname="$DB_NAME" \
                --set=ON_ERROR_STOP=1 \
                --command="INSERT INTO codecenter_schema_migrations (filename) VALUES ('$escaped_filename');"
        done < <(
            find "$directory" -maxdepth 1 -type f -name '*.sql' -print0 |
                sort -z
        )
    done
else
    echo "Aucun répertoire de migrations reconnu : migrations ignorées."
fi

echo "==> [7/8] Installation et configuration de systemd..."

# Sauvegarde de la version précédente.
sudo rm -rf -- "$BACKUP_DIR"

if [[ -d "$INSTALL_DIR" ]]; then
    sudo mv -- "$INSTALL_DIR" "$BACKUP_DIR"
fi

sudo mv -- "$STAGING_DIR" "$INSTALL_DIR"
sudo chown -R "$SERVICE_NAME:$SERVICE_NAME" "$INSTALL_DIR"

UNIT_TMP="$BUILD_DIR/${SERVICE_NAME}.service"

cat > "$UNIT_TMP" <<EOF
[Unit]
Description=Imperial CodeCenter API
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=$SERVICE_NAME
Group=$SERVICE_NAME
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_DEST
Environment=PYTHONUNBUFFERED=1
Environment=CODECENTER_DATA_DIR=$DATA_DIR
# Les fichiers créés par le service sont modifiables par le groupe (voir docs/permissions.md).
UMask=0002
# --no-proxy-headers : c'est l'middleware de l'application (CODECENTER_PROXY_MODE) qui
# décide, avec liste de proxys de confiance, quels en-têtes X-Forwarded-* croire.
ExecStart=$INSTALL_DIR/api/.venv/bin/python -m uvicorn main:app --app-dir $INSTALL_DIR/api/src --host $API_HOST --port $API_PORT --no-proxy-headers
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

sudo install -o root -g root -m 644 "$UNIT_TMP" "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo "==> [8/8] Démarrage et vérification..."

if ! sudo systemctl restart "$SERVICE_NAME"; then
    echo "Erreur au démarrage. Consulte les journaux :" >&2
    sudo journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
    die "La nouvelle version n'a pas démarré. La version précédente est conservée dans $BACKUP_DIR."
fi

sleep 2

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
    die "Le service n'est pas actif. La version précédente est conservée dans $BACKUP_DIR."
fi

echo
echo "Release terminée."
echo "Service : $SERVICE_NAME"
echo "API : http://$API_HOST:$API_PORT"
echo "Documentation : http://$API_HOST:$API_PORT/docs"
echo "Watcher : $WATCH_PATH"
echo "Ancienne version : $BACKUP_DIR"
echo
echo "Journaux : sudo journalctl -u $SERVICE_NAME -f"