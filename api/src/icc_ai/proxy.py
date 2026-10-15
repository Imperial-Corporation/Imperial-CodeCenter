"""Intégration derrière un reverse proxy (nginx) et/ou Cloudflare.

Désactivée par défaut. Se règle avec des variables d'environnement :

* `CODECENTER_PROXY_MODE`  : `off` (défaut) | `nginx` | `cloudflare`
* `CODECENTER_TRUSTED_PROXIES` : CIDR supplémentaires de proxys de confiance
  (séparés par des virgules). Le bouclage 127.0.0.1 / ::1 est toujours de
  confiance dès que le mode n'est pas `off`.
* `CODECENTER_PUBLIC_HOST` : nom public (ex. `code.exemple.fr`) ajouté aux
  hôtes acceptés pour la vérification d'origine des routes d'administration.
* `CODECENTER_ALLOWED_HOSTS` : liste d'hôtes acceptés (protège contre les
  en-têtes Host forgés) ; vide = pas de contrôle.
* `CODECENTER_FORCE_HTTPS=1` : envoie HSTS quand la requête est en HTTPS.
* `CODECENTER_RATE_LIMIT` : requêtes par minute et par IP sur `/api/`
  (défaut 240 derrière un proxy, 0 pour désactiver).

Règle d'or : les en-têtes `X-Forwarded-*`, `CF-Connecting-IP`… ne sont crus
QUE si la connexion TCP directe vient d'un proxy de confiance. Sinon ils
sont ignorés, car n'importe quel client peut les forger.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# Plages officielles (https://www.cloudflare.com/ips/). À rafraîchir avec
# scripts/update_cloudflare_ips.sh ; un fichier `cloudflare-ips.txt` à côté
# du service (CODECENTER_CLOUDFLARE_IPS_FILE) prend le dessus.
CLOUDFLARE_NETWORKS = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)
LOOPBACK = ("127.0.0.0/8", "::1/128")

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$|^\[[0-9A-Fa-f:]+\](:\d{1,5})?$")
MODES = ("off", "nginx", "cloudflare")


def _networks(items: Iterable[str]):
    result = []
    for item in items:
        item = item.strip()
        if not item or item.startswith("#"):
            continue
        try:
            result.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("CIDR ignoré (invalide) : %s", item[:60])
    return result


def _hostname(host: str) -> str:
    """Nom d'hôte sans port, en minuscules (`[::1]:8000` -> `[::1]`)."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end != -1 else ""
    return host.split(":")[0]


def _parse_ip(value: str | None):
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and "]" in value:  # [::1]:1234
        value = value[1:value.index("]")]
    elif value.count(":") == 1:  # 1.2.3.4:1234
        value = value.split(":")[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


class ProxySettings:
    def __init__(self, mode: str = "off", trusted: Iterable[str] = (), public_host: str = "",
                 allowed_hosts: Iterable[str] = (), force_https: bool = False,
                 rate_limit: int | None = None, cloudflare_networks: Iterable[str] | None = None):
        self.mode = mode if mode in MODES else "off"
        if mode not in MODES:
            logger.warning("CODECENTER_PROXY_MODE inconnu (%s) : proxy désactivé.", mode)
        self.public_host = public_host.strip().lower()
        self.allowed_hosts = [h.strip().lower() for h in allowed_hosts if h.strip()]
        self.force_https = force_https
        trusted_networks = list(_networks(trusted))
        if self.mode != "off":
            trusted_networks += _networks(LOOPBACK)
        self.cloudflare = _networks(cloudflare_networks if cloudflare_networks is not None else CLOUDFLARE_NETWORKS) \
            if self.mode == "cloudflare" else []
        self.trusted = trusted_networks + self.cloudflare
        self.rate_limit = rate_limit if rate_limit is not None else (240 if self.mode != "off" else 0)

    @classmethod
    def from_environment(cls) -> "ProxySettings":
        env = os.environ
        mode = env.get("CODECENTER_PROXY_MODE", "off").strip().lower() or "off"
        cloudflare = None
        file = env.get("CODECENTER_CLOUDFLARE_IPS_FILE", "").strip()
        if file and Path(file).is_file():
            cloudflare = Path(file).read_text(encoding="utf-8").splitlines()
        rate = env.get("CODECENTER_RATE_LIMIT", "").strip()
        return cls(
            mode=mode,
            trusted=env.get("CODECENTER_TRUSTED_PROXIES", "").split(","),
            public_host=env.get("CODECENTER_PUBLIC_HOST", ""),
            allowed_hosts=env.get("CODECENTER_ALLOWED_HOSTS", "").split(","),
            force_https=env.get("CODECENTER_FORCE_HTTPS", "").strip().lower() in ("1", "true", "yes", "oui"),
            rate_limit=int(rate) if rate.isdigit() else None,
            cloudflare_networks=cloudflare,
        )

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def is_trusted(self, peer: str | None) -> bool:
        ip = _parse_ip(peer)
        return bool(ip and any(ip in net for net in self.trusted))

    def is_cloudflare(self, peer: str | None) -> bool:
        ip = _parse_ip(peer)
        return bool(ip and any(ip in net for net in self.cloudflare))

    def client_ip(self, peer: str | None, headers: dict[str, str]) -> str | None:
        """Adresse réelle du client, ou None si on doit garder celle du pair."""
        if not self.enabled or not self.is_trusted(peer):
            return None
        if self.mode == "cloudflare":
            candidate = _parse_ip(headers.get("cf-connecting-ip"))
            if candidate:
                return str(candidate)
        forwarded = [p for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        addresses = [ip for ip in (_parse_ip(p) for p in forwarded) if ip]
        # Chaîne lue de droite à gauche : le premier maillon non fiable est le client.
        for ip in reversed(addresses):
            if not any(ip in net for net in self.trusted):
                return str(ip)
        if addresses:
            return str(addresses[0])
        real = _parse_ip(headers.get("x-real-ip"))
        return str(real) if real else None

    def scheme(self, peer: str | None, headers: dict[str, str]) -> str | None:
        if not self.enabled or not self.is_trusted(peer):
            return None
        visitor = headers.get("cf-visitor")
        if visitor and self.mode == "cloudflare":
            try:
                value = json.loads(visitor).get("scheme")
                if value in ("http", "https"):
                    return value
            except (ValueError, AttributeError):
                pass
        value = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        return value if value in ("http", "https") else None

    def forwarded_host(self, peer: str | None, headers: dict[str, str]) -> str | None:
        if not self.enabled or not self.is_trusted(peer):
            return None
        host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
        return host if host and _HOST_RE.fullmatch(host) else None


class _RateLimiter:
    """Fenêtre glissante d'une minute par IP (mémoire du processus)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> int:
        """0 si autorisé, sinon secondes à attendre."""
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return max(1, int(60 - (now - window[0])) + 1)
        window.append(now)
        if now - self._last_sweep > 300:  # purge des IP inactives
            self._last_sweep = now
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                self._hits.pop(k, None)
        return 0


SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
}
CSP = (b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       b"connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_CSP_EXEMPT = ("/docs", "/redoc", "/openapi.json")


def _csp_exempt(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _CSP_EXEMPT)


class ProxyMiddleware:
    """Middleware ASGI : IP/schéma/hôte réels, hôtes autorisés, limitation de débit, en-têtes."""

    def __init__(self, app, settings: ProxySettings | None = None):
        self.app = app
        self.settings = settings or ProxySettings.from_environment()
        self._limiter = _RateLimiter(self.settings.rate_limit) if self.settings.rate_limit > 0 else None

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        settings = self.settings
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        peer = scope["client"][0] if scope.get("client") else None

        real_ip = settings.client_ip(peer, headers)
        if real_ip:
            scope["client"] = (real_ip, 0)
        scheme = settings.scheme(peer, headers)
        if scheme:
            scope["scheme"] = "wss" if scope["type"] == "websocket" and scheme == "https" else (
                "ws" if scope["type"] == "websocket" else scheme)
        host = settings.forwarded_host(peer, headers)
        if host:
            scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"host"] + [(b"host", host.encode("latin-1"))]
            headers["host"] = host

        # Hôtes autorisés (contrôle optionnel).
        if settings.allowed_hosts:
            hostname = _hostname(headers.get("host", ""))
            if not hostname or (hostname not in settings.allowed_hosts and hostname != settings.public_host):
                await self._plain(scope, send, 400, b"Hote non autorise.")
                return

        # Limitation de débit sur l'API.
        path = scope.get("path", "")
        if self._limiter and path.startswith("/api/") and scope["type"] == "http":
            wait = self._limiter.check(scope["client"][0] if scope.get("client") else "?")
            if wait:
                await self._plain(scope, send, 429, b"Trop de requetes.", [(b"retry-after", str(wait).encode())])
                return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        https = scope.get("scheme") == "https"

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS.items() if k not in existing]
                if b"content-security-policy" not in existing and not _csp_exempt(path):
                    extra.append((b"content-security-policy", CSP))
                if settings.force_https and https and b"strict-transport-security" not in existing:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _plain(scope, send, status: int, body: bytes, headers=()):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()), *headers]})
        await send({"type": "http.response.body", "body": body})
