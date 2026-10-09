#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="$ROOT/engine"
API="$ROOT/api"
WHEELS="$ENGINE/target/wheels"

echo "==> [1/4] Compilation du moteur Rust..."

mkdir -p "$WHEELS"
rm -f "$WHEELS"/*.whl

(
    cd "$ENGINE"
    uv run --with maturin maturin build \
        --release \
        --out "$WHEELS"
)

wheels=("$WHEELS"/*.whl)

if [[ ! -f "${wheels[0]}" ]]; then
    echo "ERREUR : aucun wheel Rust n'a été généré."
    exit 1
fi

echo "==> [2/4] Préparation de l'environnement Python..."

uv sync --project "$API"

echo "==> [3/4] Installation du moteur Rust..."

uv pip install \
    --python "$API/.venv/bin/python" \
    "${wheels[@]}"

echo "==> [4/4] Vérification du module..."

"$API/.venv/bin/python" -c \
    "import codecenter_engine; print(codecenter_engine.healt())"

echo "==> Démarrage d'Imperial CodeCenter..."

exec uv run \
    --project "$API" \
    --no-sync \
    -- \
    "$API/.venv/bin/uvicorn" \
    --app-dir "$API/src" \
    main:app \
    --host 127.0.0.1 \
    --port 8000