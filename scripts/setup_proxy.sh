#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Active ou désactive l'intégration nginx / Cloudflare (production).
#
#   ./scripts/setup_proxy.sh enable --mode cloudflare --server-name code.exemple.fr
#   ./scripts/setup_proxy.sh enable --mode nginx --server-name code.exemple.fr \
#         --tls-cert /etc/letsencrypt/live/code.exemple.fr/fullchain.pem \
#         --tls-key  /etc/letsencrypt/live/code.exemple.fr/privkey.pem
#   ./scripts/setup_proxy.sh disable
#   ./scripts/setup_proxy.sh status
#   ./scripts/setup_proxy.sh print --mode cloudflare --server-name code.exemple.fr   (n'installe rien)
#
# Options de `enable` :
#   --mode nginx|cloudflare   nginx seul, ou Cloudflare devant nginx
#   --server-name NOM         nom public (obligatoire)
#   --tls-cert/--tls-key      active HTTPS sur nginx (sinon HTTP : Cloudflare gère alors le TLS)
#   --no-lock                 mode cloudflare : ne pas restreindre l'origine aux IP Cloudflare
#   --no-ipv6                 serveur sans IPv6
#   --max-upload-mb N         taille maximale d'un envoi (défaut 60)
#   --no-cloudflared          mode cloudflare : n'installe pas cloudflared
#   --tunnel-token JETON      mode cloudflare : installe cloudflared ET le service du tunnel
#                             (jeton fourni par Cloudflare Zero Trust > Tunnels ; non conservé)
#
# `enable` : génère la configuration nginx, la teste (`nginx -t`) AVANT de l'activer
# (retour arrière automatique si elle échoue), règle le service (CODECENTER_PROXY_MODE…)
# et le redémarre. `disable` remet tout à l'état d'origine (accès direct sur le port local).
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
[[ -f "$ROOT/config/deploy.conf" ]] && source "$ROOT/config/deploy.conf"
SERVICE_NAME="${SERVICE_NAME:-imperial-codecenter}"
INSTALL_DIR="${INSTALL_DIR:-/opt/imperial-codecenter}"
API_PORT="${API_PORT:-${PORT:-8000}}"
ENV_DEST="/etc/${SERVICE_NAME}/.env"
NGINX_CONF="/etc/nginx/conf.d/${SERVICE_NAME}.conf"
BACKUP_CONF="${NGINX_CONF}.disabled"

die() { echo "Erreur : $*" >&2; exit 1; }
SUDO=""; ((EUID == 0)) || SUDO="sudo"

ACTION="${1:-}"; shift || true
MODE=""; SERVER_NAME=""; TLS_CERT=""; TLS_KEY=""; EXTRA=(); WANT_CLOUDFLARED=1; TUNNEL_TOKEN=""
while (($#)); do
    case "$1" in
        --mode) MODE="${2:?}"; shift 2 ;;
        --server-name) SERVER_NAME="${2:?}"; shift 2 ;;
        --tls-cert) TLS_CERT="${2:?}"; shift 2 ;;
        --tls-key) TLS_KEY="${2:?}"; shift 2 ;;
        --max-upload-mb) EXTRA+=(--max-upload-mb "${2:?}"); shift 2 ;;
        --no-lock|--no-ipv6) EXTRA+=("$1"); shift ;;
        --no-cloudflared) WANT_CLOUDFLARED=0; shift ;;
        --tunnel-token) TUNNEL_TOKEN="${2:?}"; shift 2 ;;
        -h|--help) awk 'NR>=5 && /^#/ {print; next} NR>5 {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "Option inconnue : $1" ;;
    esac
done

# shellcheck source=/dev/null
source "$ROOT/scripts/lib/toolchain.sh"

PYTHON="python3"
[[ -x "$INSTALL_DIR/api/.venv/bin/python" ]] && PYTHON="$INSTALL_DIR/api/.venv/bin/python"
API_SRC="$ROOT/api/src"; [[ -d "$API_SRC/icc_ai" ]] || API_SRC="$INSTALL_DIR/api/src"

render() {
    local args=(--mode "$MODE" --server-name "$SERVER_NAME" --port "$API_PORT")
    [[ -n "$TLS_CERT" ]] && args+=(--tls-cert "$TLS_CERT" --tls-key "$TLS_KEY")
    local ips="$ROOT/config/proxy/cloudflare-ips.txt"
    [[ -f "$ips" ]] && args+=(--networks-file "$ips")
    PYTHONPATH="$API_SRC" "$PYTHON" -m icc_ai.proxy_config "${args[@]}" "${EXTRA[@]}"
}

set_env() { # set_env CLE VALEUR|-  (- supprime la clé)
    $SUDO python3 - "$ENV_DEST" "$1" "$2" <<'PY'
import sys
from pathlib import Path
path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
lines = [l for l in lines if not l.strip().startswith(f"{key}=")]
if value != "-":
    lines.append(f'{key}="{value}"')
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

restart_service() {
    if $SUDO systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
        $SUDO systemctl restart "$SERVICE_NAME"
        echo "==> Service $SERVICE_NAME redémarré."
    else
        echo "Avertissement : service $SERVICE_NAME absent ; lance d'abord release.sh." >&2
    fi
}

case "$ACTION" in
print)
    [[ -n "$MODE" && -n "$SERVER_NAME" ]] || die "--mode et --server-name sont requis."
    render ;;

enable)
    [[ "$MODE" == "nginx" || "$MODE" == "cloudflare" ]] || die "--mode doit valoir nginx ou cloudflare."
    [[ -n "$SERVER_NAME" ]] || die "--server-name est requis."
    command -v nginx >/dev/null 2>&1 || die "nginx n'est pas installé (sudo apt install nginx)."
    [[ -f "$ENV_DEST" ]] || die "$ENV_DEST introuvable : lance d'abord release.sh."

    # Cloudflare utilisé : cloudflared (tunnel) est installé automatiquement.
    if [[ "$MODE" == "cloudflare" ]] && ((WANT_CLOUDFLARED)); then
        ensure_cloudflared
        [[ -n "$TUNNEL_TOKEN" ]] && install_cloudflared_service "$TUNNEL_TOKEN"
    fi

    TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
    render > "$TMP"

    # Sauvegarde pour pouvoir revenir en arrière si `nginx -t` échoue.
    PREVIOUS=""; if [[ -f "$NGINX_CONF" ]]; then PREVIOUS="$(mktemp)"; $SUDO cp "$NGINX_CONF" "$PREVIOUS"; fi
    $SUDO install -m 644 -o root -g root "$TMP" "$NGINX_CONF"
    if ! $SUDO nginx -t; then
        if [[ -n "$PREVIOUS" ]]; then $SUDO install -m 644 "$PREVIOUS" "$NGINX_CONF"; else $SUDO rm -f "$NGINX_CONF"; fi
        die "Configuration nginx invalide : ancienne configuration rétablie."
    fi
    $SUDO systemctl enable nginx >/dev/null 2>&1 || true
    $SUDO systemctl reload nginx 2>/dev/null || $SUDO systemctl restart nginx
    echo "==> nginx configuré : $NGINX_CONF"

    set_env CODECENTER_PROXY_MODE "$MODE"
    set_env CODECENTER_PUBLIC_HOST "$SERVER_NAME"
    set_env CODECENTER_ALLOWED_HOSTS "$SERVER_NAME,localhost,127.0.0.1"
    if [[ -n "$TLS_CERT" || "$MODE" == "cloudflare" ]]; then set_env CODECENTER_FORCE_HTTPS 1; else set_env CODECENTER_FORCE_HTTPS -; fi
    [[ -f "$INSTALL_DIR/config/proxy/cloudflare-ips.txt" ]] && set_env CODECENTER_CLOUDFLARE_IPS_FILE "$INSTALL_DIR/config/proxy/cloudflare-ips.txt"
    restart_service
    echo
    echo "Proxy activé (mode $MODE) pour $SERVER_NAME."
    echo "Vérification : curl -s http://127.0.0.1:${API_PORT}/api/v1/proxy/check   (accès direct)"
    echo "               curl -s https://$SERVER_NAME/api/v1/proxy/check            (via le proxy)"
    if [[ "$MODE" == "cloudflare" ]]; then
        echo "Cloudflare : proxy orange activé, SSL/TLS en 'Full (strict)' si tu as un certificat sur nginx."
        if [[ -n "$TUNNEL_TOKEN" ]]; then
            echo "Tunnel : dans Cloudflare Zero Trust, fais pointer le nom public vers http://localhost:80 (nginx)."
        elif ((WANT_CLOUDFLARED)); then
            echo "cloudflared est installé. Pour un tunnel : ./scripts/setup_proxy.sh enable --mode cloudflare --server-name $SERVER_NAME --tunnel-token <jeton>"
        fi
    fi ;;

disable)
    set_env CODECENTER_PROXY_MODE -
    set_env CODECENTER_PUBLIC_HOST -
    set_env CODECENTER_ALLOWED_HOSTS -
    set_env CODECENTER_FORCE_HTTPS -
    set_env CODECENTER_CLOUDFLARE_IPS_FILE -
    if [[ -f "$NGINX_CONF" ]]; then
        $SUDO mv "$NGINX_CONF" "$BACKUP_CONF"
        $SUDO nginx -t >/dev/null 2>&1 && $SUDO systemctl reload nginx || true
        echo "==> Configuration nginx mise de côté : $BACKUP_CONF"
    fi
    restart_service
    echo "Proxy désactivé : l'API répond de nouveau seule sur le port ${API_PORT}." ;;

status)
    MODE_NOW="$($SUDO grep -s '^CODECENTER_PROXY_MODE=' "$ENV_DEST" | cut -d= -f2 | tr -d '"' || true)"
    echo "Mode proxy : ${MODE_NOW:-off}"
    [[ -f "$NGINX_CONF" ]] && echo "nginx : configuré ($NGINX_CONF)" || echo "nginx : aucune configuration"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/api/v1/proxy/check" 2>/dev/null || echo "(API injoignable)"
        echo
    fi ;;

*) awk 'NR>=5 && /^#/ {print; next} NR>5 {exit}' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
