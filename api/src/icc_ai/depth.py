# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Profondeur de recherche : répondre simplement d'abord, approfondir ensuite.

Une question générale (« comment faire une API en Python ? ») mérite une
réponse simple et utilisable, pas une exploration de la sécurité, du
chiffrement ou du déploiement que personne n'a demandée. Ce module décide,
d'après la question et l'historique, de l'un de trois niveaux :

* `overview` : question générale au premier tour -> peu de sources, peu
  d'outils, exemple minimal, puis pistes d'approfondissement proposées ;
* `focused`  : question précise -> comportement normal ;
* `deep`     : l'utilisateur demande d'aller plus loin -> recherche large,
  sources spécialisées, tous les outils autorisés.

Le choix peut être forcé par le client (`depth` = overview | focused | deep).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Sequence

Level = Literal["overview", "focused", "deep"]

# Formulations qui demandent d'aller plus loin ou d'entrer dans le détail.
_DEEP_CUES = re.compile(
    r"\b(approfond\w*|en detail|plus de detail\w*|detaill\w*|explique en profondeur|"
    r"avance\w*|expert|poussee?|complet(?:e)?\b|exhaustif\w*|exhaustiv\w*|"
    r"en profondeur|aller plus loin|va plus loin|plus loin|creuse\w*|"
    r"in depth|in-depth|deep dive|more detail\w*|advanced|go deeper|elaborate)\b"
)
# Sujets annexes : ils comptent comme « précis » s'ils sont demandés explicitement.
_SPECIFIC_CUES = re.compile(
    r"(\w+\(\)|\.\w{1,4}\b|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w+\b|`[^`]+`|"
    r"\b(chiffr\w*|crypt\w*|aes|rsa|jwt|oauth\w*|authentifi\w*|hash\w*|ssl|tls|"
    r"docker\w*|kubernetes|async\w*|thread\w*|optimis\w*|performance|securi\w*|test\w*|"
    r"migration\w*|cache|websocket\w*|graphql|orm)\b)"
)

GUIDANCE = {
    "overview": (
        "MODE APERÇU. La question est générale : donne d'abord une réponse SIMPLE, complète et directement "
        "utilisable (un exemple minimal qui fonctionne), en t'appuyant sur 1 à 3 sources génériques. "
        "N'explore PAS les sujets annexes que l'utilisateur n'a pas demandés (sécurité, chiffrement, "
        "authentification, déploiement, optimisation, tests avancés…) : ne les cherche pas et ne les développe "
        "pas. Utilise au plus 2 appels d'outils. Termine en proposant 2 à 4 pistes d'approfondissement dans "
        "`follow_ups` (questions courtes que l'utilisateur pourra poser ensuite)."
    ),
    "focused": (
        "MODE CIBLÉ. Réponds précisément à la question posée avec les sources pertinentes et reste dans le sujet "
        "demandé. Tu peux proposer 0 à 3 pistes dans `follow_ups`."
    ),
    "deep": (
        "MODE APPROFONDI. L'utilisateur veut aller plus loin : cherche activement les sources spécialisées "
        "utiles, compare-les, explique les détails, les variantes et les points d'attention. "
        "`follow_ups` est facultatif."
    ),
}


@dataclass(frozen=True)
class Depth:
    level: Level
    search_limit: int | None  # plafond du nombre de résultats d'index
    max_attached: int  # fichiers joints d'emblée au contexte
    tool_cap: int | None  # plafond d'appels d'outils (None = réglage du fournisseur)
    keep_ratio: float = 0.25  # un résultat n'est gardé que s'il vaut ce part du meilleur score
    attach_ratio: float = 0.45  # et n'est joint au contexte que s'il en vaut cette part

    @property
    def guidance(self) -> str:
        return GUIDANCE[self.level]


_LEVELS = {
    "overview": Depth("overview", search_limit=6, max_attached=2, tool_cap=2, keep_ratio=0.35, attach_ratio=0.6),
    "focused": Depth("focused", search_limit=None, max_attached=5, tool_cap=None, keep_ratio=0.25, attach_ratio=0.45),
    "deep": Depth("deep", search_limit=None, max_attached=8, tool_cap=None, keep_ratio=0.15, attach_ratio=0.3),
}


def select_relevant(hits: Sequence[dict], level: "Depth") -> tuple[list[dict], set[str]]:
    """Ne garde que les résultats réellement utiles.

    Retourne (résultats gardés, identifiants de documents à joindre au
    contexte). Le meilleur résultat est toujours gardé et joint ; les autres
    doivent atteindre une part de son score (voir `keep_ratio`/`attach_ratio`).
    Un fichier peu pertinent n'est ni montré ni envoyé au modèle : moins de
    bruit, moins de tokens, réponses plus justes.
    """
    scored = [(float(h.get("score") or 0.0), h) for h in hits]
    if not scored:
        return [], set()
    top = max(score for score, _ in scored)
    if top <= 0:
        kept = [h for _, h in scored[:1]]
        return kept, {str(h["document_id"]) for h in kept}
    kept, attach = [], set()
    for score, hit in scored:
        ratio = score / top
        if ratio >= level.keep_ratio or not kept:
            kept.append(hit)
            if ratio >= level.attach_ratio or len(attach) == 0:
                attach.add(str(hit["document_id"]))
    return kept, attach


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def assess(query: str, history: Sequence[tuple[str, str]] = (), requested: str = "auto") -> Depth:
    """Choisit le niveau de profondeur (le client peut l'imposer)."""
    if requested in _LEVELS:
        return _LEVELS[requested]

    normalized = _normalize(query)
    if _DEEP_CUES.search(normalized):
        return _LEVELS["deep"]

    words = re.findall(r"\w+", normalized)
    specific = bool(_SPECIFIC_CUES.search(query)) or bool(_SPECIFIC_CUES.search(normalized))

    # Suite d'une conversation : le sujet est déjà posé, on reste ciblé.
    if history:
        return _LEVELS["deep"] if len(words) > 18 and specific else _LEVELS["focused"]

    # Premier tour : court et sans demande précise -> aperçu.
    if len(words) <= 14 and not specific:
        return _LEVELS["overview"]
    return _LEVELS["focused"]
