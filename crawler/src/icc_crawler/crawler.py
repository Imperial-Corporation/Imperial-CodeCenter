"""Orchestration : recherche de dépôts → arborescence → téléchargement → rangement.

Le crawler s'arrête proprement quand un budget est épuisé (heure, durée, volume,
taille de la bibliothèque, espace disque) ou quand on le lui demande
(`request_stop`). Un dépôt interrompu n'est jamais marqué « terminé » : il sera
repris à la prochaine exécution, sans doublon (la base SQLite sait quels fichiers
ont déjà été enregistrés).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .classify import classify
from .filters import (
    GITHUB_LANGUAGE_NAMES, FilterConfig, content_allowed, language_of, license_allowed,
    path_allowed, rank_path,
)
from .github import GitHubClient, GitHubError, RateLimitExceeded
from .storage import Store

logger = logging.getLogger(__name__)

GIB = 1024 ** 3
META_DIR = ".crawler"

# Valeurs possibles de Stats.stop_reason.
STOP_REASONS = (
    "termine", "heure-atteinte", "duree-atteinte", "volume-atteint", "bibliotheque-pleine",
    "disque-presque-plein", "arret-demande", "limite-fichiers", "limite-de-debit", "erreur",
)

_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


# -- budgets ----------------------------------------------------------------------


@dataclass
class Clock:
    """Horloges injectables (tests) : `monotonic` pour les durées, `now` pour l'heure absolue."""
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def _existing_ancestor(path: Path) -> Path:
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def directory_size(root: Path, *, skip_meta: bool = True) -> int:
    """Taille totale des fichiers sous `root` (hors `.crawler/` par défaut)."""
    total = 0
    for current, directories, files in os.walk(root):
        if skip_meta and Path(current) == Path(root):
            directories[:] = [d for d in directories if d != META_DIR]
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                pass
    return total


@dataclass
class Budget:
    """Limites d'une exécution. `None` = pas de limite (sauf `min_free_bytes`)."""
    stop_at: datetime | None = None          # instant absolu (tz-aware ; un datetime naïf = heure locale)
    max_seconds: float | None = None         # durée maximale de l'exécution
    max_bytes: int | None = None             # octets écrits PAR CETTE EXÉCUTION
    max_library_bytes: int | None = None     # taille du dossier destination (existant inclus, hors .crawler/)
    min_free_bytes: int = 2 * GIB            # espace libre à préserver sur le disque de la destination
    clock: Clock = field(default_factory=Clock)

    def __post_init__(self) -> None:
        if self.stop_at is not None and self.stop_at.tzinfo is None:
            self.stop_at = self.stop_at.astimezone()
        self._started: float | None = None
        self._library_base = 0

    def start(self, destination: Path) -> None:
        self._started = self.clock.monotonic()
        self._library_base = 0
        if self.max_library_bytes is not None and Path(destination).is_dir():
            self._library_base = directory_size(Path(destination))

    def elapsed(self) -> float:
        return 0.0 if self._started is None else max(0.0, self.clock.monotonic() - self._started)

    def check_time(self) -> str | None:
        if self.stop_at is not None and self.clock.now() >= self.stop_at:
            return "heure-atteinte"
        if self.max_seconds is not None and self.elapsed() >= self.max_seconds:
            return "duree-atteinte"
        return None

    def check_write(self, size: int, written: int, destination: Path) -> str | None:
        """Raison d'arrêt si écrire `size` octets de plus dépasse un budget, sinon None."""
        if self.max_bytes is not None and written + size > self.max_bytes:
            return "volume-atteint"
        if self.max_library_bytes is not None and self._library_base + written + size > self.max_library_bytes:
            return "bibliotheque-pleine"
        if self.min_free_bytes > 0:
            free = shutil.disk_usage(_existing_ancestor(destination)).free
            if free - size < self.min_free_bytes:
                return "disque-presque-plein"
        return None

    def describe(self) -> dict:
        """Résumé sérialisable (événement `start` de --progress-json)."""
        return {
            "stop_at": self.stop_at.isoformat() if self.stop_at else None,
            "max_seconds": self.max_seconds,
            "max_bytes": self.max_bytes,
            "max_library_bytes": self.max_library_bytes,
            "min_free_bytes": self.min_free_bytes,
        }


# -- configuration et statistiques ----------------------------------------------------


@dataclass
class CrawlConfig:
    destination: Path
    languages: list[str] = field(default_factory=list)   # slugs : python, rust…
    query: str = ""
    topics: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)       # "owner/nom" explicites
    min_stars: int = 100
    max_repos: int = 20
    max_files_per_repo: int = 15
    max_files: int = 200
    dry_run: bool = False
    filters: FilterConfig = field(default_factory=FilterConfig)


@dataclass
class Stats:
    repos_seen: int = 0
    repos_skipped: int = 0       # déjà à jour (même commit)
    repos_done: int = 0          # explorés jusqu'au bout
    files_saved: int = 0
    files_skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "termine"
    bytes_written: int = 0
    elapsed_seconds: float = 0.0

    def skip(self, reason: str) -> None:
        self.files_skipped[reason] = self.files_skipped.get(reason, 0) + 1

    @property
    def skipped_total(self) -> int:
        return sum(self.files_skipped.values())

    def as_dict(self) -> dict:
        return {
            "repos_seen": self.repos_seen,
            "repos_skipped": self.repos_skipped,
            "repos_done": self.repos_done,
            "files_saved": self.files_saved,
            "files_skipped": dict(self.files_skipped),
            "bytes_written": self.bytes_written,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stop_reason": self.stop_reason,
            "errors": list(self.errors[:20]),
        }


class _Stop(Exception):
    """Arrêt propre demandé par un budget, une limite ou l'extérieur."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Crawler:
    def __init__(
        self,
        config: CrawlConfig,
        client: GitHubClient,
        store: Store | None,
        *,
        budget: Budget | None = None,
        stop_event: threading.Event | None = None,
        on_progress: Callable[[Stats], None] | None = None,
        echo: Callable[[str], None] = print,
    ):
        self.config = config
        self.client = client
        self.store = store
        self.budget = budget or Budget()
        self.stats = Stats()
        self._stop = stop_event or threading.Event()
        self._on_progress = on_progress
        self._echo = echo
        self._seen_hashes: set[str] = set()   # doublons dans une simulation (rien n'est stocké)

    # -- arrêt et budgets ----------------------------------------------------------

    def request_stop(self) -> None:
        """Demande un arrêt gracieux (appelable depuis un handler de signal ou un autre thread)."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def _checkpoint(self) -> None:
        """À appeler avant chaque requête réseau lourde."""
        if self._stop.is_set():
            raise _Stop("arret-demande")
        reason = self.budget.check_time()
        if reason:
            raise _Stop(reason)

    def _check_write(self, size: int) -> None:
        """À appeler avant chaque écriture (ou téléchargement d'un fichier de `size` octets)."""
        self._checkpoint()
        if self.config.dry_run:
            return
        reason = self.budget.check_write(size, self.stats.bytes_written, self.config.destination)
        if reason:
            raise _Stop(reason)

    def _notify(self) -> None:
        if self._on_progress:
            self.stats.elapsed_seconds = self.budget.elapsed()
            self._on_progress(self.stats)

    # -- découverte ---------------------------------------------------------------

    def _search_queries(self) -> list[str]:
        base = [self.config.query.strip()] if self.config.query.strip() else []
        base += [f"topic:{topic}" for topic in self.config.topics]
        base += [f"stars:>={self.config.min_stars}", "fork:false", "archived:false"]

        languages = self.config.languages or [""]
        queries = []
        for slug in languages:
            parts = list(base)
            if slug:
                parts.append(f'language:"{GITHUB_LANGUAGE_NAMES[slug]}"')
            queries.append(" ".join(parts))
        return queries

    def discover(self) -> list[dict]:
        """Dépôts candidats : explicites d'abord, puis résultats de recherche (licence filtrée)."""
        found: dict[str, dict] = {}

        for full_name in self.config.repos:
            if not _REPO_NAME.match(full_name) or ".." in full_name.split("/"):
                self.stats.errors.append(f"{full_name} : nom de dépôt invalide (attendu : propriétaire/nom)")
                continue
            self._checkpoint()
            try:
                found[full_name.lower()] = self.client.get_repository(full_name)
            except RateLimitExceeded:
                raise
            except GitHubError as exc:
                self.stats.errors.append(f"{full_name} : {exc}")

        wants_search = bool(self.config.languages or self.config.query.strip() or self.config.topics
                            or not self.config.repos)
        if wants_search:
            per_language = max(1, -(-self.config.max_repos // max(1, len(self.config.languages))))
            for query in self._search_queries():
                collected = 0
                page = 1
                while collected < per_language and page <= 10:
                    self._checkpoint()
                    try:
                        items = self.client.search_repositories(query, page=page)
                    except RateLimitExceeded:
                        raise
                    except GitHubError as exc:
                        self.stats.errors.append(f"recherche « {query} » : {exc}")
                        break
                    if not items:
                        break
                    for item in items:
                        spdx = (item.get("license") or {}).get("spdx_id")
                        if item.get("fork") or item.get("archived"):
                            continue
                        if not license_allowed(spdx, self.config.filters):
                            continue
                        key = item["full_name"].lower()
                        if key not in found:
                            found[key] = item
                            collected += 1
                        if collected >= per_language:
                            break
                    page += 1

        return list(found.values())[: max(self.config.max_repos, len(self.config.repos))]

    # -- exploration d'un dépôt -----------------------------------------------------

    def _select_paths(self, tree: list[dict]) -> list[dict]:
        candidates = []
        for entry in tree:
            if entry.get("type") != "blob":
                continue
            ok, reason = path_allowed(entry["path"], int(entry.get("size") or 0), self.config.filters)
            if ok:
                candidates.append(entry)
            elif reason != "extension":
                self.stats.skip(reason)
        candidates.sort(key=lambda e: (-rank_path(e["path"], int(e.get("size") or 0)), e["path"]))
        return candidates

    def crawl_repo(self, repo: dict) -> int:
        """Explore un dépôt. Retourne le nombre de fichiers enregistrés (ou simulés)."""
        full_name = repo["full_name"]
        spdx = (repo.get("license") or {}).get("spdx_id")
        stars = int(repo.get("stargazers_count") or 0)

        if not license_allowed(spdx, self.config.filters):
            self.stats.errors.append(f"{full_name} : licence non autorisée ({spdx or 'aucune'})")
            return 0

        self._checkpoint()
        branch = repo.get("default_branch") or "main"
        commit = self.client.get_head_commit(full_name, branch)

        if self.store and self.store.repo_done(full_name, commit):
            self.stats.repos_skipped += 1
            return 0

        self._checkpoint()
        tree, truncated = self.client.get_tree(full_name, commit)
        if truncated:
            logger.warning("%s : arborescence tronquée par GitHub", full_name)

        topics = list(repo.get("topics") or [])
        description = str(repo.get("description") or "")
        saved = 0
        license_saved = False

        for entry in self._select_paths(tree):
            if saved >= self.config.max_files_per_repo:
                break
            if self.stats.files_saved >= self.config.max_files:
                raise _Stop("limite-fichiers")

            path = entry["path"]
            size = int(entry.get("size") or 0)

            # Reprise : ce chemin a déjà été enregistré à ce commit (même si le moteur a
            # déplacé le fichier). Il compte pour le quota du dépôt, sans retéléchargement.
            if self.store and self.store.has_path(full_name, commit, path):
                saved += 1
                self.stats.skip("deja-enregistre")
                continue

            self._check_write(size)
            data = self.client.get_raw(full_name, commit, path)
            if data is None:
                self.stats.skip("introuvable")
                self._notify()
                continue

            ok, reason, text = content_allowed(data, self.config.filters)
            if not ok:
                self.stats.skip(reason)
                self._notify()
                continue

            digest = hashlib.sha256(data).hexdigest()
            if digest in self._seen_hashes or (self.store and self.store.known_hash(digest)):
                self.stats.skip("doublon")
                self._notify()
                continue

            language = language_of(path) or "autre"
            category = classify(path, topics, description, text or "")

            if self.config.dry_run:
                self._seen_hashes.add(digest)
                self._echo(f"[simulation] {full_name}/{path} -> {category}/ ({language})")
            else:
                assert self.store is not None
                self._check_write(len(data))
                if not license_saved:
                    license_text = self.client.get_license_text(full_name)
                    self.store.save_license(full_name, license_text, spdx)
                    license_saved = True
                target, written = self.store.save_file(
                    repo=full_name, commit_sha=commit, path=path, license_id=spdx,
                    language=language, category=category, data=data,
                )
                if target is None:      # contenu apparu entre-temps : doublon
                    self.stats.skip("doublon")
                    self._notify()
                    continue
                self.stats.bytes_written += written
            saved += 1
            self.stats.files_saved += 1
            self._notify()

        if self.store and not self.config.dry_run:
            self.store.mark_repo(full_name, commit, spdx, stars, "done", saved)
        self.stats.repos_done += 1
        return saved

    # -- exécution -------------------------------------------------------------------

    def run(self) -> Stats:
        self.budget.start(self.config.destination)
        reason = "termine"
        try:
            for repo in self.discover():
                self._checkpoint()
                if self.stats.files_saved >= self.config.max_files:
                    raise _Stop("limite-fichiers")
                self.stats.repos_seen += 1
                try:
                    self.crawl_repo(repo)
                except (_Stop, RateLimitExceeded):
                    raise
                except GitHubError as exc:
                    self.stats.errors.append(f"{repo.get('full_name')} : {exc}")
                self._notify()
        except _Stop as stop:
            reason = stop.reason
        except RateLimitExceeded as exc:
            reason = "limite-de-debit"
            self.stats.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - l'état reste cohérent, on rend la main proprement
            reason = "erreur"
            logger.debug("Erreur inattendue", exc_info=True)
            message = f"{type(exc).__name__} : {exc}"
            self.stats.errors.append(self.client.redact(message))
        self.stats.stop_reason = reason
        self.stats.elapsed_seconds = self.budget.elapsed()
        return self.stats
