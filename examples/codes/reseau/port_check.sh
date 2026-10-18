#!/usr/bin/env bash
# Teste si un port TCP répond, sans dépendre de nc (utilise /dev/tcp de bash).
# Usage : ./port_check.sh hôte port [délai_en_secondes]
set -u

host="${1:-127.0.0.1}"
port="${2:-80}"
timeout_s="${3:-2}"

if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    echo "port invalide : $port" >&2
    exit 2
fi

if timeout "$timeout_s" bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then
    echo "$host:$port ouvert"
else
    echo "$host:$port fermé ou injoignable"
    exit 1
fi
