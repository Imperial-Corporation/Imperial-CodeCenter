#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="$ROOT/engine"
API="$ROOT/api"
ENV_FILE="$ROOT/config/.env"

die() {
    echo "Erreur : $*" >&2
    exit 1
}

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo."
[[ -f "$ENGINE/Cargo.toml" ]] || die "engine/Cargo.toml introuvable."
[[ -f "$API/pyproject.toml" ]] || die "api/pyproject.toml introuvable."
[[ -f "$API/src/main.py" ]] || die "api/src/main.py introuvable."
[[ -f "$ENV_FILE" ]] || die "config/.env introuvable."

if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    UV_BIN="$HOME/.local/bin/uv"
else
    die "uv est introuvable. Lance d'abord ./scripts/deploy.sh."
fi

# shellcheck source=/dev/null
source "$ROOT/scripts/lib/toolchain.sh"
ensure_rust   # installe rustup, rustc et cargo s'ils manquent

echo "=== Imperial CodeCenter : mode développement ==="

echo "==> [1/3] Préparation de l'environnement Python..."

"$UV_BIN" sync --project "$API"

PYTHON="$API/.venv/bin/python"
[[ -x "$PYTHON" ]] || die "L'environnement Python de l'API est introuvable."

echo "==> [2/3] Compilation et installation du moteur Rust..."

(
    cd "$ENGINE"
    "$UV_BIN" run --with maturin maturin develop \
        --manifest-path "$ENGINE/Cargo.toml" \
        --python "$PYTHON"
)

echo "==> [3/3] Démarrage de l'API en mode développement..."

echo "API : http://127.0.0.1:8000"
echo "Documentation : http://127.0.0.1:8000/docs"
echo "Arrêt : Ctrl+C"
echo

cd "$ROOT"

exec "$UV_BIN" run \
    --project "$API" \
    --env-file "$ENV_FILE" \
    uvicorn main:app \
    --app-dir "$API/src" \
    --reload \
    --host 127.0.0.1 \
    --port 8000