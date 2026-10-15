"""Interface commune des connecteurs et types de messages neutres.

Chaque connecteur traduit ces types vers le protocole de son fournisseur :
les API ne partagent ni le format des messages, ni celui des appels d'outils.
"""

from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config.store import ProviderRuntime
from ..errors import ProviderError


@dataclass
class ToolSpec:
    """Description d'un outil, indépendante du fournisseur (JSON Schema)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Renseigné si le fournisseur a renvoyé des arguments inexploitables.
    arguments_error: str | None = None


@dataclass
class Message:
    """Message neutre : system | user | assistant | tool."""

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # nom de l'outil pour un message `tool`
    # Données brutes à rejouer telles quelles (ex. signatures Gemini).
    provider_data: Any = None


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall]
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    provider_data: Any = None


@dataclass
class ConnectionReport:
    ok: bool
    message: str
    latency_ms: int
    models: list[str] = field(default_factory=list)


def parse_json_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Convertit des arguments d'outil (chaîne JSON ou dict) en dict."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}, "Arguments d'outil non valides (JSON attendu)."
    if not isinstance(parsed, dict):
        return {}, "Les arguments d'outil doivent être un objet JSON."
    return parsed, None


class ChatProvider(abc.ABC):
    """Connecteur de fournisseur IA."""

    def __init__(
        self,
        settings: ProviderRuntime,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self._transport = transport

    # -- à implémenter ----------------------------------------------------

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        allow_tools: bool = True,
    ) -> AssistantTurn:
        """Un tour de conversation.

        `allow_tools=False` interdit au modèle d'appeler un outil tout en
        gardant leur définition (certaines API l'exigent dès que l'historique
        contient des appels d'outils).
        """

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Noms de modèles disponibles (sert aussi de test de connexion)."""

    # -- utilitaires partagés ----------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            float(self.settings.advanced.timeout_s), connect=min(10.0, float(self.settings.advanced.timeout_s))
        )
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=False)

    def _redact(self, text: str) -> str:
        """Retire la clé API et borne la longueur d'un message d'erreur."""
        key = self.settings.api_key
        if key:
            text = text.replace(key, "***")
        text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1***", text)
        return text[:300]

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Envoie une requête, traduit les échecs en ProviderError."""
        try:
            async with self._client() as client:
                response = await client.request(
                    method, url, headers=headers, json=json_body
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Le fournisseur n'a pas répondu dans le délai imparti."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network",
                self._redact(f"Impossible de joindre le fournisseur ({type(exc).__name__})."),
            ) from exc

        if response.status_code >= 400:
            raise self._error_from_response(response)

        data = self._parse_body(response)

        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "Réponse du fournisseur inattendue.")
        return data

    def _parse_body(self, response: httpx.Response) -> dict:
        """Décode le corps d'une réponse réussie (surchargeable par protocole)."""
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", self._describe_bad_body(response)) from exc

    def _describe_bad_body(self, response: httpx.Response) -> str:
        """Message de diagnostic quand le corps n'est pas du JSON.

        Indique le type de contenu et un court début du texte (clé retirée),
        pour distinguer une mauvaise URL (page web) d'un format inattendu.
        """
        content_type = response.headers.get("content-type", "inconnu").split(";")[0].strip()
        text = re.sub(r"<[^>]+>", " ", response.text or "")
        snippet = self._redact(" ".join(text.split()))[:120]

        message = f"Réponse du fournisseur illisible (JSON attendu, reçu : {content_type or 'inconnu'})."
        if "html" in content_type.lower() or response.text.lstrip().lower().startswith(("<!doctype", "<html")):
            message += (
                " C'est une page web : l'URL de l'API est probablement incorrecte "
                "(elle doit pointer vers l'API, par exemple http://127.0.0.1:20128/v1)."
            )
        elif snippet:
            message += f" Début de la réponse : « {snippet} »."
        return message

    def _error_from_response(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        detail = ""
        try:
            body = response.json()
            error = body.get("error", body) if isinstance(body, dict) else body
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("error") or "")
            else:
                detail = str(error)
        except ValueError:
            detail = response.text

        detail = self._redact(detail.strip())
        suffix = f" : {detail}" if detail else ""

        if status in (401, 403):
            return ProviderError("auth", f"Clé API refusée ou accès interdit{suffix}", status)
        if status == 404:
            return ProviderError(
                "not_found", f"Modèle ou point d'accès introuvable{suffix}", status
            )
        if status == 429:
            return ProviderError("rate_limit", f"Limite de requêtes atteinte{suffix}", status)
        if status in (408, 504):
            return ProviderError("timeout", f"Délai dépassé côté fournisseur{suffix}", status)
        if status >= 500:
            return ProviderError("server", f"Erreur du fournisseur ({status}){suffix}", status)
        return ProviderError("bad_request", f"Requête refusée ({status}){suffix}", status)

    async def test_connection(self) -> ConnectionReport:
        """Vérifie l'accès et liste les modèles quand le fournisseur le permet."""
        import time

        started = time.perf_counter()
        models = await self.list_models()
        latency = int((time.perf_counter() - started) * 1000)

        model = self.settings.model
        if models and model and model not in models and f"models/{model}" not in models:
            return ConnectionReport(
                True,
                f"Connexion réussie, mais le modèle « {model} » n'apparaît pas dans la liste.",
                latency,
                models,
            )
        return ConnectionReport(True, "Connexion réussie.", latency, models)
