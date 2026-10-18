# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Garde-fou contre le SSRF pour les URL de fournisseurs IA.

Chaque compte choisit l'URL de son fournisseur : le serveur ne doit pas
devenir un relais vers son réseau interne.

* Toujours refusé : adresses de lien local (dont les métadonnées cloud
  169.254.169.254), non spécifiées, multicast, réservées.
* `CODECENTER_PROVIDER_NETWORK` :
  - `any` (défaut en usage local) : le bouclage et les réseaux privés restent
    permis, pour Ollama, LM Studio, OmniRoute… sur la même machine ou le LAN ;
  - `public` (défaut dès que `CODECENTER_PROXY_MODE` n'est pas `off`, c'est-à-dire
    pour une instance exposée) : seules les adresses publiques sont permises.

Limite connue : la résolution DNS est vérifiée au moment de l'enregistrement,
du test et de chaque requête, mais pas verrouillée jusqu'à la connexion
(rebinding DNS théorique).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from .errors import ConfigError


class ForbiddenProviderUrl(ConfigError):
    """URL refusée par le garde-fou (422 côté API)."""


def _network_mode() -> str:
    mode = os.environ.get("CODECENTER_PROVIDER_NETWORK", "").strip().lower()
    if mode in {"any", "public"}:
        return mode
    return "public" if os.environ.get("CODECENTER_PROXY_MODE", "off").strip().lower() not in {"", "off"} else "any"


def _addresses(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []  # nom introuvable : l'erreur réseau habituelle sera signalée plus tard
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    return found


def check_provider_url(url: str) -> None:
    """Lève `ConfigError` si l'URL vise une adresse interdite."""
    host = (urlsplit(url).hostname or "").strip()
    if not host:
        raise ConfigError("URL de fournisseur invalide.")
    public_only = _network_mode() == "public"
    for ip in _addresses(host):
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:          # ::ffff:a.b.c.d se juge comme l'IPv4 qu'elle encapsule
            ip = mapped
        # ::1 (localhost en IPv6) est « réservé » pour Python mais reste du local légitime (Ollama…).
        if not ip.is_loopback and (ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
            raise ForbiddenProviderUrl("Cette adresse n'est pas autorisée pour un fournisseur IA.")
        if ip.is_loopback or ip.is_private:
            if public_only:
                raise ForbiddenProviderUrl(
                    "Les adresses locales ou privées sont refusées sur cette instance "
                    "(CODECENTER_PROVIDER_NETWORK=public) : utilise un fournisseur accessible publiquement.")
