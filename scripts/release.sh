#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/config/deploy.conf"
SERVICE_TEMPLATE="$ROOT/config/imperial-codecenter.service"
MIGRATIONS="$ROOT/config/database/migrations"
ENV_FILE="$ROOT/config/.env"

[[ -f "$CONFIG" ]] || {
    echo "Erreur : config/deploy.conf introuvable." >&2
    exit 1
}

# shellcheck source=/dev/null
source "$CONFIG"

ENV_DEST="/etc/${SERVICE_NAME}/.env"

[[ -f "$SERVICE_TEMPLATE" ]] || {
    echo "Erreur : modèle systemd introuvable." >&2
    exit 1
}

[[ -d "$MIGRATIONS" ]] || {
    echo "Erreur : dossier des migrations SQL introuvable." >&2
    exit 1
}

[[ -f "$ENV_FILE" ]] || {
    echo "Erreur : config/.env introuvable." >&2
    exit 1
}

[[ ! -L "$ENV_FILE" ]] || {
    echo "Erreur : config/.env ne doit pas être un lien symbolique." >&2
    exit 1
}

ENGINE="$ROOT/engine"
API="$ROOT/api"
FRONTEND="$ROOT/frontend"
WHEELS="$ENGINE/target/wheels"

STAGING_DIR="${INSTALL_DIR}.staging"
BACKUP_DIR="${INSTALL_DIR}.previous"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

die() {
    echo "ERREUR : $*" >&2
    exit 1
}

# Lit DATABASE_URL et extrait le mot de passe PostgreSQL.
# Python sert uniquement à analyser correctement l'URL et ses encodages.
# Le mot de passe n'est jamais affiché dans les logs.
read_database_password() {
    local encoded_password

    encoded_password="$(
        python3 - "$ENV_FILE" "$DB_USER" "$DB_NAME" <<'PY'
import base64
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

env_file, expected_user, expected_database = sys.argv[1:]

value = None

for line in Path(env_file).read_text(encoding="utf-8").splitlines():
    line = line.strip()

    if not line or line.startswith("#"):
        continue

    if line.startswith("DATABASE_URL="):
        value = line.split("=", 1)[1].strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        break

if not value:
    sys.exit("DATABASE_URL absente de config/.env")

try:
    url = urlsplit(value)
    username = unquote(url.username or "")
    password = url.password
    database = unquote(url.path.lstrip("/"))
except (ValueError, UnicodeError):
    sys.exit("DATABASE_URL invalide")

if url.scheme not in ("postgresql", "postgres"):
    sys.exit("DATABASE_URL doit utiliser PostgreSQL")

if username != expected_user:
    sys.exit("L'utilisateur de DATABASE_URL ne correspond pas à DB_USER")

if database != expected_database:
    sys.exit("La base de DATABASE_URL ne correspond pas à DB_NAME")

if password is None or password == "":
    sys.exit("DATABASE_URL doit contenir un mot de passe")

# Base64 évite que les caractères spéciaux du mot de passe
# soient interprétés par le shell pendant le transfert.
print(base64.b64encode(unquote(password).encode("utf-8")).decode("ascii"))
PY
    )" || die "Impossible de lire ou de valider DATABASE_URL."

    DATABASE_PASSWORD="$(
        printf '%s' "$encoded_password" | base64 --decode
    )" || die "Impossible de décoder le mot de passe PostgreSQL."

    [[ -n "$DATABASE_PASSWORD" ]] ||
        die "Le mot de passe PostgreSQL est vide."
}

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo."

command -v sudo >/dev/null || die "sudo est requis."
command -v curl >/dev/null || die "curl est requis."
command -v psql >/dev/null || die "psql est absent : lance deploy.sh."
command -v systemctl >/dev/null || die "systemd est requis."
command -v cargo >/dev/null || die "Rust est absent : lance deploy.sh."
command -v rustc >/dev/null || die "rustc est absent : lance deploy.sh."
command -v python3 >/dev/null || die "python3 est requis."

if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    UV_BIN="$HOME/.local/bin/uv"
else
    die "uv est absent : lance ./scripts/deploy.sh."
fi

[[ "$SERVICE_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "SERVICE_NAME invalide."

[[ "$DB_NAME" =~ ^[a-zA-Z0-9_]+$ ]] ||
    die "DB_NAME invalide."

[[ "$DB_USER" =~ ^[a-zA-Z0-9_-]+$ ]] ||
    die "DB_USER invalide."

[[ "$HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]] ||
    die "HOST invalide."

[[ "$PORT" =~ ^[0-9]+$ ]] || die "PORT invalide."
(( PORT >= 1 && PORT <= 65535 )) || die "PORT invalide."

[[ "$HEALTH_RETRIES" =~ ^[0-9]+$ ]] ||
    die "HEALTH_RETRIES invalide."

[[ "$HEALTH_INTERVAL" =~ ^[0-9]+$ ]] ||
    die "HEALTH_INTERVAL invalide."

[[ "$INSTALL_DIR" == /* && "$DATA_DIR" == /* ]] ||
    die "INSTALL_DIR et DATA_DIR doivent être absolus."

[[ "$INSTALL_DIR" != "/" && "$DATA_DIR" != "/" ]] ||
    die "Chemin racine interdit."

[[ "$INSTALL_DIR" != *"/../"* && "$DATA_DIR" != *"/../"* ]] ||
    die "Chemin contenant un segment parent interdit."

[[ "$INSTALL_DIR" != "$DATA_DIR" ]] ||
    die "L'installation et les données doivent être séparées."

[[ "$INSTALL_DIR" != "$ROOT" ]] ||
    die "INSTALL_DIR ne doit pas être le dépôt source."

[[ -f "$ENGINE/Cargo.toml" ]] ||
    die "engine/Cargo.toml introuvable."

[[ -f "$API/pyproject.toml" ]] ||
    die "api/pyproject.toml introuvable."

[[ -f "$API/src/main.py" ]] ||
    die "api/src/main.py introuvable."

[[ -f "$FRONTEND/index.html" ]] ||
    die "frontend/index.html introuvable."

# Valide l'URL avant de commencer les opérations de déploiement.
DATABASE_PASSWORD=""
read_database_password

sudo systemctl is-active --quiet postgresql ||
    die "PostgreSQL est arrêté : lance ./scripts/deploy.sh."

echo "=== Imperial CodeCenter : release ==="

echo "==> [1/8] Compilation du moteur Rust..."

mkdir -p "$WHEELS"
rm -f "$WHEELS"/*.whl

(
    cd "$ENGINE"
    "$UV_BIN" run --with maturin maturin build \
        --release \
        --out "$WHEELS"
)

shopt -s nullglob
wheels=("$WHEELS"/*.whl)
shopt -u nullglob

((${#wheels[@]} > 0)) ||
    die "Aucun wheel Rust généré."

echo "==> [2/8] Préparation des fichiers..."

sudo rm -rf -- "$STAGING_DIR"
sudo install -d "$STAGING_DIR/api" "$STAGING_DIR/frontend"

sudo install -m 644 \
    "$API/pyproject.toml" \
    "$STAGING_DIR/api/pyproject.toml"

sudo cp -a "$API/src" "$STAGING_DIR/api/src"
sudo cp -a "$FRONTEND/." "$STAGING_DIR/frontend/"

if [[ -f "$API/uv.lock" ]]; then
    sudo install -m 644 "$API/uv.lock" "$STAGING_DIR/api/uv.lock"
fi

sudo install -d "$STAGING_DIR/config/database/migrations"
sudo cp -a "$MIGRATIONS/." "$STAGING_DIR/config/database/migrations/"
sudo chown -R root:root "$STAGING_DIR"

echo "==> [3/8] Préparation du stockage..."

if ! id "$SERVICE_NAME" >/dev/null 2>&1; then
    sudo useradd \
        --system \
        --home-dir "$DATA_DIR" \
        --shell /usr/sbin/nologin \
        "$SERVICE_NAME"
fi

sudo install -d \
    -o "$SERVICE_NAME" -g "$SERVICE_NAME" \
    "$DATA_DIR" \
    "$DATA_DIR/codes" \
    "$DATA_DIR/imports" \
    "$DATA_DIR/cache" \
    "$DATA_DIR/tmp"

for directory in \
    "algorithms/c" \
    "algorithms/rust" \
    "algorithms/python" \
    "networking/c" \
    "networking/rust" \
    "networking/python" \
    "operating-systems/c" \
    "operating-systems/rust" \
    "uncategorized/unknown"
do
    sudo install -d \
        -o "$SERVICE_NAME" -g "$SERVICE_NAME" \
        "$DATA_DIR/codes/$directory"
done

echo "==> [4/8] Vérification de PostgreSQL..."

# Crée le rôle s'il n'existe pas encore.
if ! sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" |
    grep -q 1
then
    sudo -u postgres psql \
        --set=ON_ERROR_STOP=1 \
        --command="CREATE ROLE \"$DB_USER\" LOGIN;"
fi

# Synchronise le mot de passe du rôle avec DATABASE_URL.
# Les apostrophes sont doublées conformément à la syntaxe SQL.
# Le mot de passe n'est pas passé dans les arguments de psql.
DATABASE_PASSWORD_SQL="$(
    printf '%s' "$DATABASE_PASSWORD" | sed "s/'/''/g"
)" || die "Impossible de préparer le mot de passe PostgreSQL."

sudo -u postgres psql \
    --set=ON_ERROR_STOP=1 <<SQL
ALTER ROLE "$DB_USER" WITH LOGIN PASSWORD '$DATABASE_PASSWORD_SQL';
SQL

unset DATABASE_PASSWORD DATABASE_PASSWORD_SQL

# Crée la base si nécessaire.
if ! sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" |
    grep -q 1
then
    sudo -u postgres createdb \
        --owner="$DB_USER" \
        "$DB_NAME"
fi

# Assure au rôle applicatif la propriété de la base et du schéma public.
sudo -u postgres psql \
    --dbname="$DB_NAME" \
    --set=ON_ERROR_STOP=1 \
    --command="ALTER DATABASE \"$DB_NAME\" OWNER TO \"$DB_USER\";" \
    --command="ALTER SCHEMA public OWNER TO \"$DB_USER\";"

echo "==> [5/8] Application des migrations SQL..."

sudo -u postgres psql \
    --dbname="$DB_NAME" \
    --set=ON_ERROR_STOP=1 \
    --command="SET ROLE \"$DB_USER\"" \
    --command="CREATE TABLE IF NOT EXISTS public.schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );"

migration_files=("$MIGRATIONS"/*.sql)
((${#migration_files[@]} > 0)) ||
    die "Aucune migration SQL trouvée."

for migration in "${migration_files[@]}"; do
    [[ -f "$migration" ]] || continue

    filename="$(basename "$migration")"
    version="${filename%.sql}"

    [[ "$version" =~ ^[a-zA-Z0-9_-]+$ ]] ||
        die "Nom de migration invalide : $filename"

    applied="$(
        sudo -u postgres psql \
            --dbname="$DB_NAME" \
            --tuples-only \
            --no-align \
            --set=ON_ERROR_STOP=1 \
            --command="SELECT 1 FROM public.schema_migrations WHERE version = '$version';"
    )"

    if [[ "$applied" == "1" ]]; then
        echo "    Déjà appliquée : $filename"
        continue
    fi

    echo "    Application : $filename"

    migration_tmp="$(mktemp)"

    {
        echo "BEGIN;"
        cat "$migration"
        printf "\nINSERT INTO public.schema_migrations (version) VALUES ('%s');\n" "$version"
        echo "COMMIT;"
    } > "$migration_tmp"

    chmod 644 "$migration_tmp"

    if ! sudo -u postgres psql \
        --dbname="$DB_NAME" \
        --set=ON_ERROR_STOP=1 \
        --command="SET ROLE \"$DB_USER\"" \
        --file="$migration_tmp"
    then
        rm -f "$migration_tmp"
        die "Échec de la migration $filename."
    fi

    rm -f "$migration_tmp"
done

echo "==> [6/8] Installation de la nouvelle version..."

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo systemctl stop "$SERVICE_NAME"
fi

sudo rm -rf -- "$BACKUP_DIR"

if [[ -d "$INSTALL_DIR" ]]; then
    sudo mv "$INSTALL_DIR" "$BACKUP_DIR"
fi

sudo mv "$STAGING_DIR" "$INSTALL_DIR"

rollback() {
    echo "Échec du déploiement : restauration de la version précédente." >&2

    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    if [[ -d "$BACKUP_DIR" ]]; then
        sudo rm -rf -- "$INSTALL_DIR"
        sudo mv "$BACKUP_DIR" "$INSTALL_DIR"
    fi

    sudo systemctl daemon-reload
    sudo systemctl start "$SERVICE_NAME" 2>/dev/null || true
}

echo "==> [7/8] Installation des dépendances..."

if ! sudo "$UV_BIN" sync \
    --project "$INSTALL_DIR/api" \
    --no-dev
then
    rollback
    die "Échec de uv sync."
fi

if ! sudo "$UV_BIN" pip install \
    --python "$INSTALL_DIR/api/.venv/bin/python" \
    "${wheels[@]}"
then
    rollback
    die "Installation du moteur Rust impossible."
fi

if ! sudo "$INSTALL_DIR/api/.venv/bin/python" -c \
    "import codecenter_engine; print(codecenter_engine.healt())"
then
    rollback
    die "Le moteur Rust ne fonctionne pas."
fi

echo "==> [8/8] Configuration et démarrage du backend..."

sudo install -d \
    -o root -g root -m 700 \
    "$(dirname "$ENV_DEST")"

sudo install \
    -o root -g root -m 600 \
    "$ENV_FILE" "$ENV_DEST"

SERVICE_TMP="$(mktemp)"

sed \
    -e "s|@SERVICE_NAME@|$SERVICE_NAME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@DATA_DIR@|$DATA_DIR|g" \
    -e "s|@HOST@|$HOST|g" \
    -e "s|@PORT@|$PORT|g" \
    -e "s|@DB_NAME@|$DB_NAME|g" \
    -e "s|@DB_USER@|$DB_USER|g" \
    "$SERVICE_TEMPLATE" > "$SERVICE_TMP"

sudo install -m 644 "$SERVICE_TMP" "$SERVICE_FILE"
rm -f "$SERVICE_TMP"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

if ! sudo systemctl restart "$SERVICE_NAME"; then
    sudo journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
    rollback
    die "Impossible de démarrer le backend."
fi

for ((i = 0; i < HEALTH_RETRIES; i++)); do
    if curl --fail --silent \
        "http://${HOST}:${PORT}${HEALTH_ENDPOINT}" >/dev/null
    then
        echo
        echo "Imperial CodeCenter est opérationnel !"
        echo "API : http://${HOST}:${PORT}"
        echo "Documentation : http://${HOST}:${PORT}/docs"
        echo "Stockage : $DATA_DIR/codes"
        echo "Base PostgreSQL : $DB_NAME"
        echo "Logs : sudo journalctl -u $SERVICE_NAME -f"

        sudo rm -rf -- "$BACKUP_DIR"
        exit 0
    fi

    sleep "$HEALTH_INTERVAL"
done

sudo journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
rollback
die "Le backend ne répond pas après le démarrage."