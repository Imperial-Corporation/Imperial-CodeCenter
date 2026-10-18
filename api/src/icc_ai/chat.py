# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Conversations : poursuivre un échange avec l'IA après une recherche.

Chaque recherche réussie ouvre une conversation. Les messages suivants
réutilisent :

* le **registre des sources** de la conversation : `S1` désigne toujours le
  même fichier, et le texte déjà montré au modèle reste valable pour la
  vérification des citations ;
* l'**historique** des tours précédents ;
* les **mêmes outils** (recherche, lecture de fichier…) et les mêmes
  plafonds que la recherche initiale, avec une profondeur adaptée au message
  (voir `depth.py`).

Les conversations vivent en mémoire (durée de vie limitée, nombre borné) :
un redémarrage du service les efface, ce qui est signalé au client par une
erreur « conversation introuvable ».
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .conversation import new_id
from .errors import ChatError
from .response import AnswerOut, SearchResponse
from .service import SearchParams, SearchService
from .sources import Excerpt, SourceRegistry

MAX_MESSAGE_CHARS = 2000
MAX_TURNS = 20
MAX_SOURCES = 300
SESSION_TTL_S = 2 * 3600
MAX_SESSIONS = 200
HISTORY_TEXT_CHARS = 3800


@dataclass
class ChatSession:
    id: str
    registry: SourceRegistry
    history: list[tuple[str, str]] = field(default_factory=list)
    turns: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Tours affichables ({question, response}) : sert à enregistrer la discussion.
    log: list[dict] = field(default_factory=list)
    # Renseignés quand la discussion est devenue définitive (voir accounts.py).
    saved_chat_id: str | None = None
    owner_id: str | None = None
    # Compte qui a ouvert la conversation : seul lui peut la poursuivre ou l'enregistrer.
    creator_id: str | None = None


def answer_to_text(answer: AnswerOut) -> str:
    """Version texte d'une réponse, pour l'historique envoyé au modèle."""
    parts = [answer.summary]
    for section in answer.sections:
        if section.title:
            parts.append(f"### {section.title}")
        if section.body:
            parts.append(section.body)
        for block in section.code_blocks:
            parts.append(f"```{block.language}\n{block.code}\n```")
    return "\n\n".join(parts)[:HISTORY_TEXT_CHARS]


def _log_entry(question: str, response: SearchResponse) -> dict:
    return {"question": question, "response": response.model_dump(mode="json")}


class ChatService:
    def __init__(self, search: SearchService, *, ttl: float = SESSION_TTL_S, max_sessions: int = MAX_SESSIONS):
        self._search = search
        self._ttl = ttl
        self._max = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()

    # -- gestion des sessions -------------------------------------------------

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, s in self._sessions.items() if now - s.touched > self._ttl and not s.lock.locked()]:
            del self._sessions[key]
        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)

    def get(self, conversation_id: str) -> ChatSession:
        self._purge()
        session = self._sessions.get(conversation_id)
        if session is None:
            raise ChatError(
                "not_found", "Conversation introuvable ou expirée : relance une recherche pour en ouvrir une nouvelle.")
        session.touched = time.monotonic()
        self._sessions.move_to_end(conversation_id)
        return session

    def __len__(self) -> int:
        return len(self._sessions)

    # -- tours ------------------------------------------------------------------

    def check_owner(self, conversation_id: str, user_id: str | None) -> ChatSession:
        """La conversation existe et appartient à ce compte (sinon « introuvable », sans rien révéler)."""
        session = self.get(conversation_id)
        if session.creator_id != user_id:
            raise ChatError(
                "not_found", "Conversation introuvable ou expirée : relance une recherche pour en ouvrir une nouvelle.")
        return session

    async def search(self, params: SearchParams, depth: str = "auto", *, config=None,
                     user_id: str | None = None) -> SearchResponse:
        """Première recherche : ouvre une conversation si l'IA a répondu."""
        registry = SourceRegistry()
        conversation_id = new_id()
        response = await self._search.run(params, (), registry=registry, depth=depth,
                                          conversation_id=conversation_id, config=config)
        if response.status == "ok" and response.answer is not None:
            session = ChatSession(conversation_id, registry, creator_id=user_id)
            session.history = [("user", params.query), ("assistant", answer_to_text(response.answer))]
            session.log = [_log_entry(params.query, response)]
            session.turns = 1
            self._sessions[conversation_id] = session
            self._purge()  # après l'ajout : le plafond de sessions n'est jamais dépassé
        else:
            response.meta.conversation_id = ""
        return response

    async def reply(self, conversation_id: str, message: str, *, depth: str = "auto",
                    language: str | None = None, category: str | None = None, config=None,
                    user_id: str | None = None) -> SearchResponse:
        """Tour suivant d'une conversation (outils disponibles comme au premier tour)."""
        message = message.strip()
        if not message:
            raise ChatError("invalid", "Le message est vide.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ChatError("invalid", f"Message trop long (max {MAX_MESSAGE_CHARS} caractères).")

        session = self.check_owner(conversation_id, user_id)
        if session.turns >= MAX_TURNS:
            raise ChatError("too_long", "Conversation trop longue : lance une nouvelle recherche.")
        if len(session.registry) >= MAX_SOURCES:
            raise ChatError("too_long", "Trop de sources accumulées : lance une nouvelle recherche.")
        if session.lock.locked():
            raise ChatError("busy", "Une réponse est déjà en cours pour cette conversation.")

        async with session.lock:
            params = SearchParams(message, limit=5, language=language, category=category)
            response = await self._search.run(
                params, tuple(session.history), registry=session.registry, depth=depth,
                conversation_id=conversation_id, config=config,
            )
            if response.status == "ok" and response.answer is not None:
                session.history.append(("user", message))
                session.history.append(("assistant", answer_to_text(response.answer)))
                session.history = session.history[-2 * MAX_TURNS:]
                session.log.append(_log_entry(message, response))
                session.log = session.log[-MAX_TURNS:]
                session.turns += 1
            session.touched = time.monotonic()
            return response

    # -- discussions enregistrées -------------------------------------------------

    def snapshot(self, conversation_id: str, user_id: str | None = None) -> list[dict]:
        """Tours affichables d'une conversation en cours (pour l'enregistrer)."""
        session = self.check_owner(conversation_id, user_id)
        return [dict(entry) for entry in session.log]

    def mark_saved(self, conversation_id: str, chat_id: str, owner_id: str) -> None:
        session = self.get(conversation_id)
        session.saved_chat_id, session.owner_id = chat_id, owner_id

    def saved_target(self, conversation_id: str) -> tuple[str, str] | None:
        """(chat_id, owner_id) si la conversation est enregistrée, sinon None."""
        session = self._sessions.get(conversation_id)
        if session is None or not session.saved_chat_id or not session.owner_id:
            return None
        return session.saved_chat_id, session.owner_id

    def restore(self, turns: list[dict], *, chat_id: str | None = None, owner_id: str | None = None) -> str:
        """Rouvre une discussion enregistrée : registre des sources et historique reconstruits.

        Les extraits de code déjà montrés ne sont plus « vus » par le serveur :
        si le modèle les recite, ils s'afficheront « adaptés » plutôt que
        « extraits » tant que le fichier n'a pas été relu dans cette session.
        """
        registry = SourceRegistry()
        history: list[tuple[str, str]] = []
        last_sources: list[dict] = []
        for turn in turns:
            response = turn.get("response") or {}
            if response.get("sources"):
                last_sources = response["sources"]
            answer = response.get("answer")
            if answer:
                try:
                    history.append(("user", str(turn.get("question", ""))))
                    history.append(("assistant", answer_to_text(AnswerOut.model_validate(answer))))
                except ValueError:
                    history.pop()
        for item in sorted(last_sources, key=lambda s: int(str(s.get("id", "S0"))[1:] or 0)):
            source = registry.register_hit({
                "document_id": item["document_id"], "title": item.get("title", ""),
                "relative_path": item.get("path", ""), "language": item.get("language", ""),
                "language_name": item.get("language_name", ""), "categories": item.get("categories", []),
                "content_sha256": item.get("sha256", ""), "score": item.get("score", 0.0),
                "summary": item.get("summary"),
            })
            source.excerpts = [Excerpt(int(e["line_start"]), int(e["line_end"]), str(e["text"]))
                               for e in item.get("excerpts", [])]
        conversation_id = new_id()
        session = ChatSession(conversation_id, registry)
        session.history = history[-2 * MAX_TURNS:]
        session.log = [dict(t) for t in turns][-MAX_TURNS:]
        session.turns = len(turns)
        session.saved_chat_id, session.owner_id, session.creator_id = chat_id, owner_id, owner_id
        self._sessions[conversation_id] = session
        self._purge()
        return conversation_id
