# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

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

from .classify import classify_detailed
from .filters import (
    GITHUB_LANGUAGE_NAMES, FilterConfig, content_allowed, directory_pruned, language_of, license_allowed,
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
    # Mode UNSAFE : aucun plafond de dépôts, de fichiers par dépôt ni de fichiers au total.
    # Restent actifs : garde disque, exclusion des secrets, limites GitHub, arrêt demandé,
    # confinement des chemins (voir Budget pour les plafonds de durée et de taille).
    unsafe: bool = False
    # Collecte forcée : les dépôts sont réexplorés et chaque fichier est retéléchargé et comparé.
    force: bool = False

    @property
    def repo_limit(self) -> int | None:
        return None if self.unsafe else self.max_repos

    @property
    def per_repo_limit(self) -> int | None:
        return None if self.unsafe else self.max_files_per_repo

    @property
    def files_limit(self) -> int | None:
        return None if self.unsafe else self.max_files

    def describe(self) -> dict:
        """Paramètres réellement appliqués (événement `start`, affichés dans l'état de la collecte)."""
        return {
            "mode": "unsafe" if self.unsafe else "normal",
            "languages": list(self.languages),
            "query": self.query,
            "topics": list(self.topics),
            "repos": list(self.repos),
            "min_stars": self.min_stars,
            "max_repos": self.repo_limit,
            "max_files_per_repo": self.per_repo_limit,
            "max_files": self.files_limit,
            "max_file_kb": self.filters.max_bytes // 1024,
            "min_file_bytes": self.filters.min_bytes,
            "include_tests": self.filters.include_tests,
            "licenses": sorted(self.filters.licenses),
            "force": self.force,
            "dry_run": self.dry_run,
        }


@dataclass
class Stats:
    repos_seen: int = 0
    repos_skipped: int = 0       # déjà à jour (même commit, mêmes réglages)
    repos_done: int = 0          # explorés jusqu'au bout
    repos_quota_reached: int = 0  # dépôts où le quota de fichiers par dépôt a coupé l'exploration
    files_saved: int = 0         # nouveaux fichiers + fichiers mis à jour + fichiers rétablis
    files_updated: int = 0       # contenu modifié sur GitHub, mis à jour localement
    files_skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "termine"
    stop_detail: str = ""        # précision lisible : quelle limite, quelle valeur
    bytes_written: int = 0
    elapsed_seconds: float = 0.0
    trees_truncated: int = 0     # arborescences que GitHub a tronquées (parcourues dossier par dossier)
    search_windows: int = 0      # fenêtres d'étoiles supplémentaires ouvertes (au-delà des 1000 résultats)
    # Suivi en direct (événements `progress`) : où en est-on, et sur quoi ?
    phase: str = "start"          # start | discovery | exploring | done
    repos_target: int = 0         # dépôts à explorer (0 = inconnu : découverte en continu, mode UNSAFE)
    files_target: int = 0         # quota de fichiers demandé (0 = illimité)
    current_repo: str = ""
    repo_files: int = 0           # fichiers enregistrés dans le dépôt en cours
    repo_quota: int = 0           # quota par dépôt (0 = illimité)
    recent: list[str] = field(default_factory=list)   # derniers fichiers enregistrés

    def remember(self, entry: str, keep: int = 6) -> None:
        self.recent.append(entry)
        del self.recent[:-keep]

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
            "repos_quota_reached": self.repos_quota_reached,
            "files_saved": self.files_saved,
            "files_updated": self.files_updated,
            "files_skipped": dict(self.files_skipped),
            "bytes_written": self.bytes_written,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
            "trees_truncated": self.trees_truncated,
            "search_windows": self.search_windows,
            "errors": list(self.errors[:20]),
        }


class _Stop(Exception):
    """Arrêt propre demandé par un budget, une limite ou l'extérieur."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 10          # GitHub ne renvoie jamais plus de 1000 résultats par requête
SEARCH_RESULT_CAP = SEARCH_PAGE_SIZE * SEARCH_MAX_PAGES


def filter_key(config: "CrawlConfig") -> str:
    """Empreinte des réglages qui changent le *choix* des fichiers d'un dépôt.

    Si elle diffère de celle d'une exploration précédente, le dépôt est réexploré même au même commit.
    """
    f = config.filters
    raw = repr((f.min_bytes, f.max_bytes, f.max_line_length, f.include_tests,
                sorted(f.languages) if f.languages is not None else None))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


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
        self._filter_key = filter_key(config)

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
            raise _Stop("arret-demande", "arrêt demandé")
        reason = self.budget.check_time()
        if reason:
            raise _Stop(reason, "limite de durée atteinte")

    def _check_write(self, size: int) -> None:
        """À appeler avant chaque écriture (ou téléchargement d'un fichier de `size` octets)."""
        self._checkpoint()
        if self.config.dry_run:
            return
        reason = self.budget.check_write(size, self.stats.bytes_written, self.config.destination)
        if reason:
            raise _Stop(reason, "espace disque libre sous le seuil de sécurité"
                        if reason == "disque-presque-plein" else "limite de volume atteinte")

    def _notify(self) -> None:
        if self._on_progress:
            self.stats.elapsed_seconds = self.budget.elapsed()
            self._on_progress(self.stats)

    def _log(self, message: str) -> None:
        """Ligne de journal (stderr en mode JSON, affichée dans l'état de la collecte)."""
        logger.info(message)
        self._echo(message)

    # -- découverte ---------------------------------------------------------------

    def _search_queries(self, upper_stars: int | None = None) -> list[str]:
        base = [self.config.query.strip()] if self.config.query.strip() else []
        base += [f"topic:{topic}" for topic in self.config.topics]
        stars = (f"stars:{self.config.min_stars}..{upper_stars}" if upper_stars is not None
                 else f"stars:>={self.config.min_stars}")
        base += [stars, "fork:false", "archived:false"]

        languages = self.config.languages or [""]
        queries = []
        for slug in languages:
            parts = list(base)
            if slug:
                parts.append(f'language:"{GITHUB_LANGUAGE_NAMES[slug]}"')
            queries.append(" ".join(parts))
        return queries

    def _explicit_repositories(self, found: dict[str, dict]):
        for full_name in self.config.repos:
            if not _REPO_NAME.match(full_name) or ".." in full_name.split("/"):
                self.stats.errors.append(f"{full_name} : nom de dépôt invalide (attendu : propriétaire/nom)")
                continue
            self._checkpoint()
            try:
                repo = self.client.get_repository(full_name)
            except RateLimitExceeded:
                raise
            except GitHubError as exc:
                self.stats.errors.append(f"{full_name} : {exc}")
                continue
            if full_name.lower() not in found:
                found[full_name.lower()] = repo
                yield repo

    def _search_language(self, slug_index: int, found: dict[str, dict], limit: int | None):
        """Résultats de recherche pour un langage, jusqu'à `limit` nouveaux dépôts (None = jusqu'à épuisement).

        GitHub plafonne chaque recherche à 1000 résultats triés par étoiles décroissantes. Sans limite
        (ou avec une limite plus haute), on ouvre une nouvelle fenêtre `stars:min..dernier-score`.
        """
        collected = 0
        upper: int | None = None
        while limit is None or collected < limit:
            query = self._search_queries(upper)[slug_index]
            seen_in_window = 0
            last_stars: int | None = None
            for page in range(1, SEARCH_MAX_PAGES + 1):
                self._checkpoint()
                try:
                    items = self.client.search_repositories(query, page=page, per_page=SEARCH_PAGE_SIZE)
                except RateLimitExceeded:
                    raise
                except GitHubError as exc:
                    self.stats.errors.append(f"recherche « {query} » : {exc}")
                    return
                if not items:
                    break
                seen_in_window += len(items)
                for item in items:
                    last_stars = int(item.get("stargazers_count") or 0)
                    spdx = (item.get("license") or {}).get("spdx_id")
                    if item.get("fork") or item.get("archived"):
                        continue
                    if not license_allowed(spdx, self.config.filters):
                        continue
                    key = item["full_name"].lower()
                    if key in found:
                        continue
                    found[key] = item
                    collected += 1
                    yield item
                    if limit is not None and collected >= limit:
                        return
                if len(items) < SEARCH_PAGE_SIZE:
                    break
            # Fenêtre pleine (1000 résultats) : il peut y avoir d'autres dépôts moins étoilés.
            if seen_in_window < SEARCH_RESULT_CAP or last_stars is None:
                return
            next_upper = last_stars if last_stars != upper else last_stars - 1
            if next_upper < self.config.min_stars:
                return
            upper = next_upper
            self.stats.search_windows += 1
            self._log(f"Recherche : fenêtre d'étoiles {self.config.min_stars}..{upper} (au-delà de 1000 résultats)")

    def iter_candidates(self):
        """Dépôts candidats, au fil de la découverte : explicites d'abord, puis résultats de recherche.

        En mode NORMAL : au plus `max_repos` dépôts de recherche (répartis entre les langages). En mode
        UNSAFE : aucun plafond, la recherche continue jusqu'à épuisement des résultats accessibles.
        """
        found: dict[str, dict] = {}
        yield from self._explicit_repositories(found)

        wants_search = bool(self.config.languages or self.config.query.strip() or self.config.topics
                            or not self.config.repos)
        if not wants_search:
            return
        languages = max(1, len(self.config.languages))
        limit = self.config.repo_limit
        per_language = None if limit is None else max(1, -(-limit // languages))
        for index in range(languages):
            yield from self._search_language(index, found, per_language)

    def discover(self) -> list[dict]:
        """Liste des dépôts candidats (mode NORMAL : bornée par `max_repos`, explicites toujours inclus)."""
        repos = list(self.iter_candidates())
        limit = self.config.repo_limit
        return repos if limit is None else repos[: max(limit, len(self.config.repos))]

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

    def _license_text(self, full_name: str) -> str | None:
        try:
            return self.client.get_license_text(full_name)
        except RateLimitExceeded:
            raise
        except GitHubError as exc:
            self.stats.errors.append(f"{full_name} : texte de licence indisponible ({exc})")
            return None

    def crawl_repo(self, repo: dict) -> int:
        """Explore un dépôt. Retourne le nombre de fichiers enregistrés (ou simulés)."""
        full_name = repo["full_name"]
        spdx = (repo.get("license") or {}).get("spdx_id")
        stars = int(repo.get("stargazers_count") or 0)
        force = self.config.force
        per_repo = self.config.per_repo_limit
        global_limit = self.config.files_limit

        if not license_allowed(spdx, self.config.filters):
            self.stats.errors.append(f"{full_name} : licence non autorisée ({spdx or 'aucune'})")
            return 0

        self._checkpoint()
        self.stats.current_repo = full_name
        self.stats.repo_files = 0
        self._notify()
        branch = repo.get("default_branch") or "main"
        commit = self.client.get_head_commit(full_name, branch)

        if self.store and not force and self.store.repo_done(
                full_name, commit, quota=per_repo, key=self._filter_key):
            self.stats.repos_skipped += 1
            return 0

        self._checkpoint()
        tree, incomplete = self.client.get_full_tree(
            full_name, commit, prune=directory_pruned, checkpoint=self._checkpoint)
        if incomplete is not None:
            self.stats.trees_truncated += 1
            if incomplete:
                message = f"{full_name} : arborescence encore incomplète après parcours dossier par dossier"
                self.stats.errors.append(message)
                self._log(message)
            else:
                self._log(f"{full_name} : arborescence tronquée par GitHub, parcourue dossier par dossier")

        topics = list(repo.get("topics") or [])
        description = str(repo.get("description") or "")
        self.stats.current_repo = full_name
        self.stats.repo_files = 0
        self._notify()
        saved = 0
        license_saved = False
        cut_by_quota = False

        for entry in self._select_paths(tree):
            if per_repo is not None and saved >= per_repo:
                cut_by_quota = True
                break
            if global_limit is not None and self.stats.files_saved >= global_limit:
                raise _Stop("limite-fichiers", f"{global_limit} fichiers au total")

            path = entry["path"]
            size = int(entry.get("size") or 0)
            blob = str(entry.get("sha") or "")
            row = self.store.get_file(full_name, path) if self.store else None

            if row is not None and not force:
                if row["status"] == "deleted":
                    self.stats.skip("supprime-localement")
                    continue
                if row["commit_sha"] == commit:
                    # Reprise : ce chemin a déjà été enregistré à ce commit (même si le moteur l'a
                    # déplacé). Il compte pour le quota du dépôt, sans retéléchargement.
                    saved += 1
                    self.stats.skip("deja-enregistre")
                    continue
                if blob and row["blob_sha"] == blob:
                    # Nouveau commit mais même blob git : le contenu n'a pas changé.
                    self.store.touch(row["id"], commit_sha=commit, branch=branch, blob_sha=blob)
                    saved += 1
                    self.stats.skip("inchange")
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
            found = classify_detailed(path, topics, description, text or "")
            language = language_of(path) or "autre"

            if self.config.dry_run:
                if digest in self._seen_hashes:
                    self.stats.skip("doublon")
                    self._notify()
                    continue
                self._seen_hashes.add(digest)
                where = "/".join(found.folder_parts)
                self._echo(f"[simulation] {full_name}/{path} -> {where}/ ({language})")
            else:
                assert self.store is not None
                self._check_write(len(data))
                target, written = self.store.save_file(
                    repo=full_name, commit_sha=commit, path=path, license_id=spdx, language=language,
                    category=found.category, subcategory=found.subcategory, tags=found.tags,
                    branch=branch, blob_sha=blob, data=data, force=force,
                )
                action = self.store.last_action
                if action == "duplicate":
                    self.stats.skip("doublon")
                    self._notify()
                    continue
                if action == "supprime":
                    self.stats.skip("supprime-localement")
                    self._notify()
                    continue
                if action == "unchanged":
                    saved += 1              # déjà présent et identique : compte pour le quota du dépôt
                    self.stats.skip("inchange")
                    self._notify()
                    continue
                if not license_saved:
                    self.store.save_license(full_name, self._license_text(full_name), spdx)
                    license_saved = True
                if action == "updated":
                    self.stats.files_updated += 1
                self.stats.bytes_written += written
            saved += 1
            self.stats.files_saved += 1
            self.stats.repo_files = saved
            self.stats.remember(f"{full_name}/{path}")
            self._notify()

        if cut_by_quota:
            self.stats.repos_quota_reached += 1
            self._log(f"{full_name} : quota de {per_repo} fichiers par dépôt atteint")
        if self.store and not self.config.dry_run:
            self.store.mark_repo(full_name, commit, spdx, stars, "quota" if cut_by_quota else "done", saved,
                                 branch=branch, quota=per_repo if cut_by_quota else None, key=self._filter_key)
        self.stats.repos_done += 1
        return saved

    # -- exécution -------------------------------------------------------------------

    def run(self) -> Stats:
        self.budget.start(self.config.destination)
        reason, detail = "termine", ""
        unsafe = self.config.unsafe
        self.stats.files_target = self.config.files_limit or 0
        self.stats.repo_quota = self.config.per_repo_limit or 0
        try:
            self.stats.phase = "discovery"
            self._notify()
            if unsafe:
                # Découverte en continu : l'exploration commence sans attendre la fin de la recherche.
                candidates = self.iter_candidates()
            else:
                candidates = self.discover()
                self.stats.repos_target = len(candidates)
            self.stats.phase = "exploring"
            self._notify()
            for repo in candidates:
                self._checkpoint()
                limit = self.config.files_limit
                if limit is not None and self.stats.files_saved >= limit:
                    raise _Stop("limite-fichiers", f"{limit} fichiers au total")
                self.stats.repos_seen += 1
                if unsafe:
                    self.stats.repos_target = max(self.stats.repos_target, self.stats.repos_seen)
                try:
                    self.crawl_repo(repo)
                except (_Stop, RateLimitExceeded):
                    raise
                except GitHubError as exc:
                    self.stats.errors.append(f"{repo.get('full_name')} : {exc}")
                self._notify()
        except _Stop as stop:
            reason, detail = stop.reason, stop.detail
        except RateLimitExceeded as exc:
            reason, detail = "limite-de-debit", str(exc)
            self.stats.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - l'état reste cohérent, on rend la main proprement
            reason = "erreur"
            logger.debug("Erreur inattendue", exc_info=True)
            message = f"{type(exc).__name__} : {exc}"
            self.stats.errors.append(self.client.redact(message))
            detail = self.client.redact(message)
        self.stats.stop_reason = reason
        self.stats.stop_detail = detail
        self.stats.phase = "done"
        self.stats.current_repo = ""
        self.stats.elapsed_seconds = self.budget.elapsed()
        self._log(f"Fin : {reason}" + (f" ({detail})" if detail else ""))
        return self.stats
