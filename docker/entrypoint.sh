#!/bin/sh
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.
#
# Démarrage du conteneur : clé de chiffrement persistante, attente de
# PostgreSQL, migrations (une seule fois chacune), scripts d'exemple, puis l'API.
set -eu

# Secrets Docker / Podman / Kubernetes : NOM_FILE=/run/secrets/x lit la valeur dans le fichier
# (une variable déjà définie directement reste prioritaire). Liste fermée, pas de génériques.
for name in DATABASE_URL CODECENTER_ADMIN_TOKEN CODECENTER_SECRET_KEY CODECENTER_METRICS_TOKEN \
    CODECENTER_BACKUP_PASSPHRASE GITHUB_TOKEN; do
    file_var="${name}_FILE"
    eval "file_path=\${$file_var:-}"
    eval "current=\${$name:-}"
    if [ -n "$file_path" ] && [ -z "$current" ]; then
        if [ ! -r "$file_path" ]; then
            echo "$file_var : fichier illisible ($file_path)." >&2
            exit 1
        fi
        value="$(cat "$file_path")"
        export "$name=$value"
    fi
done

export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

DATA="${CODECENTER_DATA_DIR:-/data}"
MIGRATIONS="${ICC_MIGRATIONS_DIR:-/app/config/database/migrations}"

: "${DATABASE_URL:?DATABASE_URL est requis (voir compose.yaml)}"

umask 077
mkdir -p "$DATA/codes/_depot" "$DATA/imports" "$DATA/cache" "$DATA/tmp"

# Clé de chiffrement des secrets serveur : créée une fois, conservée dans le volume.
KEY_FILE="${CODECENTER_SECRET_KEY_FILE:-$DATA/secret.key}"
if [ -z "${CODECENTER_SECRET_KEY:-}" ] && [ ! -f "$KEY_FILE" ]; then
    echo "Génération de la clé de chiffrement : $KEY_FILE"
    python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())" > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
fi
umask 022

# Attente de PostgreSQL (le healthcheck du compose le fait déjà ; ceci couvre les autres usages).
tries=0
until pg_isready -q -d "$DATABASE_URL"; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
        echo "PostgreSQL ne répond pas après 60 s." >&2
        exit 1
    fi
    sleep 1
done

# Migrations : même suivi que scripts/release.sh (table codecenter_schema_migrations).
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS codecenter_schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

if [ -d "$MIGRATIONS" ]; then
    for file in $(find "$MIGRATIONS" -maxdepth 1 -type f -name '*.sql' | sort); do
        name="$(basename "$file")"
        # (psql n'interpole les variables que sur l'entrée standard, pas avec -c)
        done_already="$(echo "SELECT 1 FROM codecenter_schema_migrations WHERE filename = :'name'" |
            psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -tA -v name="$name")"
        [ "$done_already" = "1" ] && continue
        echo "Migration : $name"
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q --single-transaction -f "$file"
        echo "INSERT INTO codecenter_schema_migrations (filename) VALUES (:'name')" |
            psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -v name="$name"
    done
fi

# Scripts d'exemple : copiés une seule fois (ceux que tu supprimes ne reviennent pas).
if [ ! -f "$DATA/codes/.examples-installed" ] && [ -d /app/examples/codes ]; then
    cp -r /app/examples/codes/. "$DATA/codes/"
    : > "$DATA/codes/.examples-installed"
fi

exec "$@"
