"""Exceptions communes du package IA.

Les messages de ces exceptions sont destinés à l'utilisateur : ils ne
contiennent jamais de clé API ni de contenu sensible.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Erreur renvoyée par un fournisseur IA ou lors de l'appel réseau.

    `kind` appartient à : auth, rate_limit, timeout, network, not_found,
    bad_request, server, invalid_response, tools_unsupported.
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status


class ToolsUnsupportedError(ProviderError):
    """Le modèle actif n'accepte pas le tool-calling."""

    def __init__(self, message: str = "Ce modèle ne gère pas les outils."):
        super().__init__("tools_unsupported", message, 400)


class ConfigError(Exception):
    """Configuration absente, invalide ou illisible."""


class EngineError(Exception):
    """Erreur du moteur Rust. `kind` : invalid, not_found, forbidden, internal."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class SearchCancelled(Exception):
    """La recherche a été annulée par l'utilisateur ou par déconnexion."""


class ChatError(Exception):
    """Erreur de conversation : `kind` vaut invalid, not_found, too_long ou busy."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message
