#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/config/deploy.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Erreur : lance ce script sans sudo."
    exit 1
fi

[[ -f "$CONFIG" ]] || {
    echo "Erreur : config/deploy.conf introuvable."
    exit 1
}

# shellcheck source=/dev/null
source "$CONFIG"

if ! command -v apt-get >/dev/null 2>&1; then
    echo "Erreur : ce script nécessite Ubuntu ou Debian."
    exit 1
fi

echo "=== Imperial CodeCenter : préparation du serveur ==="

sudo apt-get update
sudo apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    libssl-dev \
    pkg-config \
    postgresql \
    postgresql-client \
    python3 \
    python3-dev \
    python3-venv

echo "=== Installation / vérification de Rust ==="

if ! command -v rustup >/dev/null 2>&1; then
    if ! command -v rustc >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -sSf \
            https://sh.rustup.rs | sh -s -- -y
    else
        echo "Rust est installé sans rustup."
        echo "Installe rustup manuellement pour gérer sa version."
        exit 1
    fi
fi

if [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
fi

rustup toolchain install stable
rustup default stable

rustc --version
cargo --version

echo "=== Installation / vérification de uv ==="

if ! command -v uv >/dev/null 2>&1; then
    if [[ -x "$HOME/.local/bin/uv" ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        curl --proto '=https' --tlsv1.2 -sSf \
            https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

command -v uv >/dev/null || {
    echo "Erreur : uv est introuvable après l'installation."
    exit 1
}

uv --version

echo "=== Préparation de PostgreSQL ==="

sudo systemctl enable --now postgresql

sudo systemctl is-active --quiet postgresql || {
    echo "Erreur : PostgreSQL n'est pas actif."
    exit 1
}

echo "Préparation terminée."
echo "Aucune base n'a été supprimée ou réinitialisée."
echo "Prochaine étape : ./scripts/release.sh"