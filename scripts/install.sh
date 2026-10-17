#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Déploie TOUT en une commande : préparation du serveur, compilation, base,
# service systemd, scripts d'exemple, permissions, zone de dépôt, crawler,
# proxy (optionnel) et diagnostic final.
#
#   ./scripts/install.sh                                   # installation locale
#   ./scripts/install.sh --generate-admin-token            # + jeton d'administration
#   ./scripts/install.sh --proxy cloudflare --server-name code.exemple.fr
#   ./scripts/install.sh --proxy nginx --server-name code.exemple.fr \
#         --tls-cert /etc/letsencrypt/live/code.exemple.fr/fullchain.pem \
#         --tls-key  /etc/letsencrypt/live/code.exemple.fr/privkey.pem
#
# Options :
#   --skip-system            ne relance pas deploy.sh (serveur déjà préparé)
#   --force-examples         réinstalle les scripts d'exemple supprimés
#   --generate-admin-token   crée CODECENTER_ADMIN_TOKEN s'il est absent (affiché une seule fois)
#   --proxy MODE             nginx | cloudflare (voir docs/proxy.md)
#   --tunnel-token JETON     avec --proxy cloudflare : installe aussi le service du tunnel cloudflared
#   --no-cloudflared         avec --proxy cloudflare : n'installe pas cloudflared
#   --server-name NOM        nom public (requis avec --proxy)
#   --tls-cert / --tls-key   HTTPS sur nginx
#   --no-doctor              saute le diagnostic final
#
# Le script est relançable : une mise à jour conserve la base, la clé de
# chiffrement, tes fichiers et ta configuration.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "Erreur : $*" >&2; exit 1; }
step() { echo; echo "######## $* ########"; }

[[ $EUID -ne 0 ]] || die "Lance ce script sans sudo (il demandera sudo quand il en a besoin)."

SKIP_SYSTEM=0; FORCE_EX=0; GEN_TOKEN=0; DOCTOR=1
PROXY=""; SERVER_NAME=""; TLS_CERT=""; TLS_KEY=""; TUNNEL_TOKEN=""; NO_CLOUDFLARED=0
while (($#)); do
    case "$1" in
        --skip-system) SKIP_SYSTEM=1; shift ;;
        --force-examples) FORCE_EX=1; shift ;;
        --generate-admin-token) GEN_TOKEN=1; shift ;;
        --no-doctor) DOCTOR=0; shift ;;
        --proxy) PROXY="${2:?}"; shift 2 ;;
        --server-name) SERVER_NAME="${2:?}"; shift 2 ;;
        --tunnel-token) TUNNEL_TOKEN="${2:?}"; shift 2 ;;
        --no-cloudflared) NO_CLOUDFLARED=1; shift ;;
        --tls-cert) TLS_CERT="${2:?}"; shift 2 ;;
        --tls-key) TLS_KEY="${2:?}"; shift 2 ;;
        -h|--help) awk 'NR>=5 && /^#/ {print; next} NR>5 {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "Option inconnue : $1 (voir --help)" ;;
    esac
done
if [[ -n "$PROXY" ]]; then
    [[ "$PROXY" == "nginx" || "$PROXY" == "cloudflare" ]] || die "--proxy doit valoir nginx ou cloudflare."
    [[ -n "$SERVER_NAME" ]] || die "--server-name est requis avec --proxy."
fi

[[ -f "$ROOT/config/deploy.conf" ]] || die "config/deploy.conf introuvable."
# shellcheck source=/dev/null
source "$ROOT/config/deploy.conf"
SERVICE_NAME="${SERVICE_NAME:-imperial-codecenter}"
ENV_DEST="/etc/${SERVICE_NAME}/.env"

step "1/6 Configuration"
if [[ ! -f "$ROOT/config/.env" ]]; then
    [[ -f "$ROOT/config/.env.exemple" ]] || die "config/.env et config/.env.exemple introuvables."
    cp "$ROOT/config/.env.exemple" "$ROOT/config/.env"
    echo "config/.env créé depuis l'exemple."
else
    echo "config/.env existant conservé."
fi

step "2/6 Préparation du serveur"
if ((SKIP_SYSTEM)); then echo "(ignorée : --skip-system)"; else "$ROOT/scripts/deploy.sh"; fi

step "3/6 Compilation, base, service"
"$ROOT/scripts/release.sh"

step "4/6 Scripts d'exemple"
if ((FORCE_EX)); then
    sudo "$ROOT/scripts/install_examples.sh" "$DATA_DIR/codes" --force
    "$ROOT/scripts/fix_permissions.sh" "$DATA_DIR/codes" "$SERVICE_NAME" --user "$USER"
else
    echo "Déjà gérés par release.sh (une seule installation ; --force-examples pour réinstaller)."
fi

step "5/6 Sécurité et proxy"
if ((GEN_TOKEN)); then
    if sudo grep -q '^CODECENTER_ADMIN_TOKEN=' "$ENV_DEST" 2>/dev/null; then
        echo "Un jeton d'administration existe déjà : conservé."
    else
        TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        echo "CODECENTER_ADMIN_TOKEN=\"$TOKEN\"" | sudo tee -a "$ENV_DEST" >/dev/null
        sudo systemctl restart "$SERVICE_NAME"
        echo "Jeton d'administration créé (note-le, il ne sera plus affiché) :"
        echo "    $TOKEN"
    fi
fi
if [[ -n "$PROXY" ]]; then
    ARGS=(enable --mode "$PROXY" --server-name "$SERVER_NAME")
    [[ -n "$TLS_CERT" ]] && ARGS+=(--tls-cert "$TLS_CERT" --tls-key "$TLS_KEY")
    ((NO_CLOUDFLARED)) && ARGS+=(--no-cloudflared)
    [[ -n "$TUNNEL_TOKEN" ]] && ARGS+=(--tunnel-token "$TUNNEL_TOKEN")
    "$ROOT/scripts/setup_proxy.sh" "${ARGS[@]}"
else
    echo "Proxy non demandé (accès direct). Pour la production : --proxy nginx|cloudflare."
fi

step "6/6 Diagnostic"
if ((DOCTOR)); then "$ROOT/scripts/doctor.sh" || echo "(le diagnostic signale des erreurs : voir ci-dessus et docs/permissions.md)"; fi

echo
echo "Installation terminée."
echo "  Interface    : http://${API_HOST:-${HOST:-127.0.0.1}}:${API_PORT:-${PORT:-8000}}"
echo "  Zone de dépôt: $DATA_DIR/codes/_depot   (scripts + tags ; voir docs/depot.md)"
echo "  Diagnostic   : ./scripts/doctor.sh"
