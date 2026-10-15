"""Client minimal de l'API REST GitHub, poli envers les limites de débit.

- jeton lu dans `GITHUB_TOKEN` (60 requêtes/h sans jeton, 5 000/h avec ;
  la recherche est limitée à 30 requêtes/min) ;
- attente automatique quand la limite est atteinte (en-têtes
  `X-RateLimit-*` et `Retry-After`), avec un plafond d'attente ;
- nouvelles tentatives avec temporisation croissante sur les erreurs 5xx
  et les coupures réseau ;
- le jeton n'est envoyé qu'à api.github.com (jamais à raw.githubusercontent.com)
  et n'est jamais écrit dans un message d'erreur.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from . import __version__

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
RAW_URL = "https://raw.githubusercontent.com"


class GitHubError(Exception):
    """Erreur de l'API (message sans secret)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimitExceeded(GitHubError):
    """La limite de débit dépasse le temps d'attente autorisé."""


class NotFound(GitHubError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_wait: float = 900.0,
        retries: int = 3,
        pause: float = 0.0,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"icc-crawler/{__version__} (Imperial CodeCenter)",
        }
        # Le jeton n'est PAS un en-tête par défaut : il n'est ajouté que pour l'API REST.
        self.authenticated = bool(token)
        self._token = token or None
        self._http = httpx.Client(headers=headers, transport=transport, timeout=30.0, follow_redirects=True)
        self._sleep = sleep
        self._clock = clock
        self._max_wait = max_wait
        self._retries = retries
        self._pause = pause
        self.requests_made = 0
        # Instant (horloge `clock`) avant lequel une ressource (core/search) est épuisée.
        self._blocked_until: dict[str, float] = {}

    def close(self) -> None:
        self._http.close()

    # -- cœur -------------------------------------------------------------------

    def redact(self, text: str) -> str:
        """Masque le jeton dans un texte destiné à un journal ou à un message."""
        return text.replace(self._token, "***") if self._token else text

    _redact = redact

    @staticmethod
    def _resource_of(url: str) -> str | None:
        """Compartiment de quota concerné : `search`, `core`, ou None hors API (raw)."""
        if not url.startswith(API_URL):
            return None
        return "search" if url.startswith(f"{API_URL}/search/") else "core"

    @staticmethod
    def _to_float(value: str | None) -> float | None:
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def _wait_for(self, seconds: float, reason: str) -> None:
        seconds = max(0.0, seconds)
        if seconds > self._max_wait:
            raise RateLimitExceeded(
                f"{reason} : attente de {int(seconds)} s supérieure au plafond ({int(self._max_wait)} s). "
                "Réessaie plus tard ou fournis un jeton GITHUB_TOKEN."
            )
        logger.info("%s : pause de %.0f s", reason, seconds)
        self._sleep(seconds + 1.0)

    def _request(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        resource = self._resource_of(url)
        request_headers = dict(headers or {})
        if self._token and resource is not None:
            request_headers["Authorization"] = f"Bearer {self._token}"

        for attempt in range(self._retries + 1):
            # Quota épuisé annoncé par une réponse précédente : on attend avant de réessayer.
            if resource is not None and resource in self._blocked_until:
                self._wait_for(self._blocked_until[resource] - self._clock(), "Quota GitHub atteint")
                del self._blocked_until[resource]
            if self._pause:
                self._sleep(self._pause)
            try:
                self.requests_made += 1
                response = self._http.get(url, params=params, headers=request_headers)
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise GitHubError(self.redact(f"Réseau indisponible ({type(exc).__name__}).")) from None
                self._sleep(2.0 ** attempt)
                continue

            status = response.status_code

            # Limite de débit primaire (403/429 avec quota épuisé) ou secondaire (Retry-After).
            if status in (403, 429):
                remaining = response.headers.get("x-ratelimit-remaining")
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.strip().isdigit():
                    self._wait_for(float(retry_after), "Limite secondaire GitHub")
                    continue
                if remaining == "0":
                    reset = self._to_float(response.headers.get("x-ratelimit-reset"))
                    if reset is None:
                        reset = self._clock() + 60.0
                    self._wait_for(reset - self._clock(), "Quota GitHub épuisé")
                    continue
                if status == 429:
                    self._wait_for(60.0 * (attempt + 1), "Trop de requêtes (429)")
                    continue
                raise GitHubError(self.redact(f"Accès refusé ({status}) : {self._message(response)}"), status)

            if status >= 500:
                if attempt == self._retries:
                    raise GitHubError(f"Erreur serveur GitHub ({status}).", status)
                self._sleep(2.0 ** attempt)
                continue

            if status == 404:
                raise NotFound("Ressource introuvable.", 404)
            if status >= 400:
                raise GitHubError(self.redact(f"Requête refusée ({status}) : {self._message(response)}"), status)

            # Quota vide après cette réponse : on conserve la réponse (déjà payée) et on
            # n'attend qu'avant la requête suivante du même compartiment.
            if resource is not None and response.headers.get("x-ratelimit-remaining") == "0":
                reset = self._to_float(response.headers.get("x-ratelimit-reset"))
                if reset is not None:
                    bucket = (response.headers.get("x-ratelimit-resource") or resource).lower()
                    self._blocked_until["search" if bucket == "search" else "core"] = reset

            return response

        raise GitHubError("Nombre maximal de tentatives atteint.")

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            return str(response.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            return response.text[:200]

    def _json(self, path: str, params: dict | None = None) -> Any:
        response = self._request(f"{API_URL}{path}", params=params)
        try:
            return response.json()
        except ValueError:
            raise GitHubError("Réponse GitHub illisible (JSON invalide).", response.status_code) from None

    # -- API de haut niveau -----------------------------------------------------

    def search_repositories(self, query: str, *, page: int = 1, per_page: int = 100) -> list[dict]:
        data = self._json("/search/repositories", {
            "q": query, "sort": "stars", "order": "desc", "page": page, "per_page": per_page,
        })
        return list(data.get("items") or []) if isinstance(data, dict) else []

    def get_repository(self, full_name: str) -> dict:
        return self._json(f"/repos/{full_name}")

    def get_head_commit(self, full_name: str, branch: str) -> str:
        data = self._json(f"/repos/{full_name}/branches/{quote(branch, safe='')}")
        return str(data["commit"]["sha"])

    def get_tree(self, full_name: str, commit_sha: str) -> tuple[list[dict], bool]:
        """Arborescence complète d'un commit : (entrées, tronquée ?)."""
        data = self._json(f"/repos/{full_name}/git/trees/{commit_sha}", {"recursive": "1"})
        if not isinstance(data, dict):
            return [], False
        return list(data.get("tree") or []), bool(data.get("truncated"))

    def get_license_text(self, full_name: str) -> str | None:
        try:
            data = self._json(f"/repos/{full_name}/license")
        except NotFound:
            return None
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        except (ValueError, AttributeError):
            return None

    def get_raw(self, full_name: str, commit_sha: str, path: str) -> bytes | None:
        """Contenu d'un fichier à un commit précis (hors quota de l'API REST)."""
        url = f"{RAW_URL}/{full_name}/{commit_sha}/{quote(path)}"
        try:
            return self._request(url, headers={"Accept": "*/*"}).content
        except NotFound:
            return None
