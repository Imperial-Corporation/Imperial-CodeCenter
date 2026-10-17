#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Installation automatique des outils de compilation et de proxy.
# À inclure avec :  source "$ROOT/scripts/lib/toolchain.sh"
#
#   ensure_rust         rustup + rustc + cargo (toolchain "stable")
#   ensure_cloudflared  binaire cloudflared (tunnel Cloudflare), si le mode Cloudflare est utilisé
#
# Chaque fonction est idempotente : si l'outil est déjà là, rien n'est téléchargé.
# Le compte courant ne doit pas être root (rustup s'installe dans ~/.cargo).

_tc_die() { echo "Erreur : $*" >&2; exit 1; }
_tc_sudo() { if ((EUID == 0)); then "$@"; else sudo "$@"; fi; }

_tc_need_curl() {
    command -v curl >/dev/null 2>&1 && return 0
    if command -v apt-get >/dev/null 2>&1; then
        _tc_sudo apt-get update -y && _tc_sudo apt-get install -y curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then _tc_sudo dnf install -y curl ca-certificates
    elif command -v pacman >/dev/null 2>&1; then _tc_sudo pacman -S --noconfirm curl ca-certificates
    elif command -v zypper >/dev/null 2>&1; then _tc_sudo zypper --non-interactive install curl ca-certificates
    fi
    command -v curl >/dev/null 2>&1 || _tc_die "curl est requis et n'a pas pu être installé."
}

_tc_cc() { # un éditeur de liens C est nécessaire à cargo
    command -v cc >/dev/null 2>&1 && return 0
    echo "==> Installation d'un compilateur C (requis par cargo)..."
    if command -v apt-get >/dev/null 2>&1; then _tc_sudo apt-get install -y build-essential pkg-config libssl-dev
    elif command -v dnf >/dev/null 2>&1; then _tc_sudo dnf install -y gcc make pkgconf-pkg-config openssl-devel
    elif command -v pacman >/dev/null 2>&1; then _tc_sudo pacman -S --noconfirm base-devel openssl
    elif command -v zypper >/dev/null 2>&1; then _tc_sudo zypper --non-interactive install gcc make pkg-config libopenssl-devel
    fi
    command -v cc >/dev/null 2>&1 || _tc_die "Aucun compilateur C (cc) : installe build-essential ou équivalent."
}

ensure_rust() {
    export PATH="$HOME/.cargo/bin:$PATH"
    if command -v rustup >/dev/null 2>&1 && command -v cargo >/dev/null 2>&1 && command -v rustc >/dev/null 2>&1; then
        # Un toolchain par défaut doit exister (rustup sans toolchain installe rien d'utilisable).
        if ! rustc --version >/dev/null 2>&1; then
            echo "==> rustup présent sans toolchain : installation de 'stable'..."
            rustup toolchain install stable --profile minimal && rustup default stable
        fi
    elif command -v rustup >/dev/null 2>&1; then
        echo "==> rustup présent : installation du toolchain stable (cargo, rustc)..."
        rustup toolchain install stable --profile minimal && rustup default stable
    else
        if command -v cargo >/dev/null 2>&1 || command -v rustc >/dev/null 2>&1; then
            echo "==> cargo/rustc système sans rustup : installation de rustup à côté (le système n'est pas modifié)."
        fi
        _tc_need_curl
        echo "==> Installation de rustup, rustc et cargo (https://sh.rustup.rs)..."
        local installer; installer="$(mktemp)"
        curl --proto '=https' --tlsv1.2 -fsSL -o "$installer" https://sh.rustup.rs \
            || { rm -f "$installer"; _tc_die "Téléchargement de rustup impossible (réseau ? accès à sh.rustup.rs). Installe-le à la main : https://rustup.rs"; }
        sh "$installer" -y --profile minimal --default-toolchain stable \
            || { rm -f "$installer"; _tc_die "Installation de rustup échouée."; }
        rm -f "$installer"
        # shellcheck source=/dev/null
        [[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    # Prend le rustup en priorité sur d'éventuels cargo/rustc système trop anciens.
    hash -r 2>/dev/null || true
    for tool in rustup rustc cargo; do
        command -v "$tool" >/dev/null 2>&1 || _tc_die "$tool introuvable après l'installation (PATH : ~/.cargo/bin)."
    done
    _tc_cc
    echo "    $(rustup --version 2>/dev/null | head -n1)"
    echo "    $(rustc --version)"
    echo "    $(cargo --version)"

    # Version minimale déclarée par le moteur (rust-version dans Cargo.toml), si présente.
    local manifest="${ROOT:-.}/engine/Cargo.toml" need have
    if [[ -f "$manifest" ]]; then
        need="$(sed -n 's/^rust-version *= *"\([0-9.]*\)".*/\1/p' "$manifest" | head -n1)"
        have="$(rustc --version | sed -n 's/^rustc \([0-9.]*\).*/\1/p')"
        if [[ -n "$need" && -n "$have" && "$(printf '%s\n%s\n' "$need" "$have" | sort -V | head -n1)" != "$need" ]]; then
            echo "==> rustc $have < $need requis : mise à jour de stable..."
            rustup update stable && rustup default stable
        fi
    fi
}

ensure_cloudflared() {
    if command -v cloudflared >/dev/null 2>&1; then
        echo "    cloudflared déjà installé : $(cloudflared --version 2>&1 | head -n1)"
        return 0
    fi
    _tc_need_curl
    echo "==> Installation de cloudflared..."
    local arch
    case "$(uname -m)" in
        x86_64|amd64) arch=amd64 ;;
        aarch64|arm64) arch=arm64 ;;
        armv7l|armv6l|arm) arch=arm ;;
        i386|i686) arch=386 ;;
        *) _tc_die "Architecture non prise en charge pour cloudflared : $(uname -m)" ;;
    esac

    if command -v apt-get >/dev/null 2>&1 && [[ -d /etc/apt ]]; then
        # Dépôt officiel Cloudflare, paquet signé.
        _tc_sudo mkdir -p --mode=0755 /usr/share/keyrings
        if curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | _tc_sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null \
            && echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
                | _tc_sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null \
            && _tc_sudo apt-get update -y && _tc_sudo apt-get install -y cloudflared; then
            :
        else
            echo "    Dépôt apt indisponible : repli sur le paquet publié sur GitHub."
            _tc_sudo rm -f /etc/apt/sources.list.d/cloudflared.list
            local tmp; tmp="$(mktemp -d)"
            curl -fsSL -o "$tmp/cloudflared.deb" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb" \
                && _tc_sudo dpkg -i "$tmp/cloudflared.deb"
            rm -rf "$tmp"
        fi
    else
        local tmp; tmp="$(mktemp -d)"
        curl -fsSL -o "$tmp/cloudflared" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}" \
            && _tc_sudo install -m 0755 "$tmp/cloudflared" /usr/local/bin/cloudflared
        rm -rf "$tmp"
    fi
    command -v cloudflared >/dev/null 2>&1 || _tc_die "Installation de cloudflared échouée."
    echo "    $(cloudflared --version 2>&1 | head -n1)"
}

# Installe cloudflared comme service systemd à partir d'un jeton de tunnel
# (Zero Trust > Réseaux > Tunnels). Le jeton n'est jamais écrit dans nos fichiers.
install_cloudflared_service() {
    local token="${1:?jeton de tunnel requis}"
    if systemctl list-unit-files 2>/dev/null | grep -q '^cloudflared\.service'; then
        echo "    Service cloudflared déjà présent : conservé (désinstalle-le avec 'sudo cloudflared service uninstall' pour le recréer)."
        return 0
    fi
    _tc_sudo cloudflared service install "$token"
}
