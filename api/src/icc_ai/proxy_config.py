"""Génère la configuration nginx pour Imperial CodeCenter.

Usage (sans rien installer ni modifier) :

    python -m icc_ai.proxy_config --mode cloudflare --server-name code.exemple.fr
    python -m icc_ai.proxy_config --mode nginx --server-name code.exemple.fr \\
        --tls-cert /etc/letsencrypt/live/code.exemple.fr/fullchain.pem \\
        --tls-key  /etc/letsencrypt/live/code.exemple.fr/privkey.pem

Le script `scripts/setup_proxy.sh` s'appuie sur ce module pour l'installer.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys

from .proxy import CLOUDFLARE_NETWORKS

_SERVER_NAME_RE = re.compile(r"^(_|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]+$")


def render_nginx(
    *, mode: str, server_name: str, upstream_port: int = 8000, tls_cert: str = "", tls_key: str = "",
    lock_to_cloudflare: bool | None = None, networks: tuple[str, ...] = CLOUDFLARE_NETWORKS,
    max_upload_mb: int = 60, ipv6: bool = True,
) -> str:
    if mode not in ("nginx", "cloudflare"):
        raise ValueError("mode doit valoir nginx ou cloudflare")
    if not _SERVER_NAME_RE.fullmatch(server_name):  # fullmatch : `$` accepterait un saut de ligne final
        raise ValueError("Nom de serveur invalide.")
    if not (1 <= upstream_port <= 65535):
        raise ValueError("Port invalide.")
    if bool(tls_cert) != bool(tls_key):
        raise ValueError("Fournis le certificat ET la clé TLS, ou aucun des deux.")
    for path in (tls_cert, tls_key):
        if path and (not _PATH_RE.fullmatch(path) or ".." in path.split("/")):
            raise ValueError("Chemin TLS invalide.")
    if not 1 <= max_upload_mb <= 500:
        raise ValueError("Taille d'envoi invalide.")
    for net in networks:
        ipaddress.ip_network(net, strict=False)

    cloudflare = mode == "cloudflare"
    lock = cloudflare if lock_to_cloudflare is None else lock_to_cloudflare
    tls = bool(tls_cert)

    out: list[str] = []
    add_raw = out.append

    def add(line: str) -> None:
        # Serveur sans IPv6 : on retire les écoutes [::].
        if not ipv6 and line.lstrip().startswith("listen [::]"):
            return
        add_raw(line)

    add("# Imperial CodeCenter : reverse proxy (généré par icc_ai.proxy_config, ne pas éditer à la main)")
    add(f"# Mode : {mode}")
    add("")
    add("map $http_upgrade $icc_connection_upgrade { default upgrade; '' close; }")
    add("limit_req_zone $binary_remote_addr zone=icc_api:10m rate=10r/s;")
    add("")
    add("upstream icc_backend {")
    add(f"    server 127.0.0.1:{upstream_port};")
    add("    keepalive 16;")
    add("}")
    add("")

    def server_body() -> list[str]:
        body: list[str] = []
        if cloudflare:
            body.append("    # IP réelle du visiteur : seulement crue venant des plages Cloudflare.")
            for net in networks:
                body.append(f"    set_real_ip_from {net};")
            body.append("    real_ip_header CF-Connecting-IP;")
            body.append("    real_ip_recursive on;")
            body.append("")
        if lock:
            body.append("    # L'origine n'accepte que Cloudflare : les connexions directes sont coupées (444).")
            body.append("    # Le test porte sur l'adresse TCP réelle ($realip_remote_addr), pas sur celle du visiteur.")
            body.append("    if ($icc_from_cloudflare = 0) { return 444; }")
            body.append("")
        body += [
            f"    client_max_body_size {max_upload_mb}m;",
            "    server_tokens off;",
            "",
            "    # Fichiers cachés (.git, .env…) : jamais servis.",
            "    location ~ /\\. { deny all; return 404; }",
            "",
            "    location /ws/ {",
            "        proxy_pass http://icc_backend;",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection $icc_connection_upgrade;",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Forwarded-For $remote_addr;",
            "        proxy_set_header X-Forwarded-Proto $icc_forwarded_proto;",
            "        proxy_read_timeout 3600s;",
            "    }",
            "",
            "    location /api/ {",
            "        limit_req zone=icc_api burst=40 nodelay;",
            "        limit_req_status 429;",
            "        proxy_pass http://icc_backend;",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Connection \"\";",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "        proxy_set_header X-Forwarded-For $remote_addr;",
            "        proxy_set_header X-Forwarded-Proto $icc_forwarded_proto;",
        ]
        if cloudflare:
            body.append("        proxy_set_header CF-Connecting-IP $remote_addr;")
        body += [
            "        # Une recherche IA peut durer jusqu'à 10 minutes.",
            "        proxy_read_timeout 660s;",
            "        proxy_send_timeout 120s;",
            "    }",
            "",
            "    location / {",
            "        proxy_pass http://icc_backend;",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Connection \"\";",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Forwarded-For $remote_addr;",
            "        proxy_set_header X-Forwarded-Proto $icc_forwarded_proto;",
            "    }",
        ]
        return body

    if lock:
        add("geo $realip_remote_addr $icc_from_cloudflare {")
        add("    default 0;")
        add("    127.0.0.1 1;")
        add("    ::1 1;")
        for net in networks:
            add(f"    {net} 1;")
        add("}")
        add("")

    # Protocole vu par le visiteur : avec Cloudflare, le lien Cloudflare -> origine peut être
    # en HTTP alors que le visiteur est en HTTPS ; CF-Visitor le dit (fiable car l'origine est verrouillée).
    if cloudflare:
        add("map $http_cf_visitor $icc_forwarded_proto {")
        add("    default $scheme;")
        add("    '~\"scheme\":\"https\"' https;")
        add("}")
    else:
        add("map $scheme $icc_forwarded_proto { default $scheme; }")
    add("")

    if tls:
        add("server {")
        add("    listen 80;")
        add("    listen [::]:80;")
        add(f"    server_name {server_name};")
        add("    location /.well-known/acme-challenge/ { root /var/www/html; }")
        add("    location / { return 301 https://$host$request_uri; }")
        add("}")
        add("")
        add("server {")
        # `listen … http2` reste accepté par toutes les versions (l'option est seulement dépréciée dès 1.25).
        add("    listen 443 ssl http2;")
        add("    listen [::]:443 ssl http2;")
        add(f"    server_name {server_name};")
        add(f"    ssl_certificate {tls_cert};")
        add(f"    ssl_certificate_key {tls_key};")
        add("    ssl_protocols TLSv1.2 TLSv1.3;")
        add("    ssl_session_cache shared:icc_ssl:10m;")
        add("    ssl_session_timeout 1d;")
        add("")
    else:
        add("server {")
        add("    listen 80;")
        add("    listen [::]:80;")
        add(f"    server_name {server_name};")
        add("")
    out.extend(server_body())
    add("}")
    add("")
    return "\n".join(out)


def render_cloudflare_ips(networks: tuple[str, ...] = CLOUDFLARE_NETWORKS) -> str:
    """Contenu du fichier `cloudflare-ips.txt` (un CIDR par ligne)."""
    return "\n".join(networks) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icc_ai.proxy_config", description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("nginx", "cloudflare"), required=True)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--port", type=int, default=8000, help="port de l'API (défaut 8000)")
    parser.add_argument("--tls-cert", default="")
    parser.add_argument("--tls-key", default="")
    parser.add_argument("--no-lock", action="store_true",
                        help="mode cloudflare : ne PAS limiter l'origine aux IP Cloudflare")
    parser.add_argument("--max-upload-mb", type=int, default=60)
    parser.add_argument("--networks-file", default="",
                        help="fichier de plages Cloudflare (un CIDR par ligne) au lieu de la liste intégrée")
    parser.add_argument("--no-ipv6", action="store_true", help="n'écoute pas sur [::] (serveur sans IPv6)")
    args = parser.parse_args(argv)
    networks = CLOUDFLARE_NETWORKS
    if args.networks_file:
        try:
            with open(args.networks_file, encoding="utf-8") as handle:
                networks = tuple(l.strip() for l in handle if l.strip() and not l.startswith("#"))
        except OSError as exc:
            print(f"Erreur : {exc}", file=sys.stderr)
            return 2
    try:
        text = render_nginx(
            mode=args.mode, server_name=args.server_name, upstream_port=args.port,
            tls_cert=args.tls_cert, tls_key=args.tls_key,
            lock_to_cloudflare=False if args.no_lock else None, max_upload_mb=args.max_upload_mb, ipv6=not args.no_ipv6, networks=networks,
        )
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
