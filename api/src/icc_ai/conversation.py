"""Modèles de données préparant une future chatbox.

Rien de tout cela n'est branché à l'interface aujourd'hui : ces classes
fixent la séparation entre trois notions pour que la chatbox puisse être
ajoutée sans réécrire le pipeline :

* `Conversation` / `ChatMessage` : l'historique persistant des échanges ;
* `SearchRun` : une requête de recherche et sa réponse (un message
  assistant référence l'exécution qui l'a produite) ;
* `ToolExecution` (dans `tools.base`) : les appels d'outils d'une exécution.

Le pipeline (`SearchService.run`) accepte déjà un `history` de messages
neutres : une conversation n'aura qu'à y fournir les tours précédents.
Un dépôt persistant (PostgreSQL, fichier…) implémentera `ConversationRepository`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .response import SearchResponse


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class SearchRun:
    """Une exécution du pipeline de recherche."""

    query: str
    response: SearchResponse
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_now)


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str
    # Pour un message assistant : l'exécution (réponse + sources + outils).
    run_id: str | None = None
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_now)


@dataclass
class Conversation:
    id: str = field(default_factory=new_id)
    title: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)

    def history(self, max_messages: int = 6) -> list[tuple[str, str]]:
        """Derniers tours (rôle, texte) à fournir au pipeline."""
        return [(m.role, m.content) for m in self.messages[-max_messages:]]


class ConversationRepository(Protocol):
    def get(self, conversation_id: str) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> None: ...
    def save_run(self, run: SearchRun) -> None: ...
    def get_run(self, run_id: str) -> SearchRun | None: ...


class InMemoryConversationRepository:
    """Implémentation de référence (tests, développement)."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._runs: dict[str, SearchRun] = {}

    def get(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def save(self, conversation: Conversation) -> None:
        self._conversations[conversation.id] = conversation

    def save_run(self, run: SearchRun) -> None:
        self._runs[run.id] = run

    def get_run(self, run_id: str) -> SearchRun | None:
        return self._runs.get(run_id)
