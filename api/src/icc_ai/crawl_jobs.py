# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Pilotage du crawler GitHub depuis l'API : lancement, planification, arrêt.

Le crawler tourne dans un sous-processus (`python -m icc_crawler`) : on peut
l'arrêter proprement (SIGTERM), il ne partage pas la mémoire de l'API et son
environnement est réduit au strict nécessaire (jamais la clé de chiffrement
ni l'URL de la base). Un seul travail à la fois.

Objectifs d'arrêt (tous optionnels, le premier atteint gagne) :
heure précise, durée en minutes, volume téléchargé en Go, taille maximale de
la bibliothèque, et un plancher d'espace disque libre toujours actif.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import crawler_bridge
from .config.crypto import SecretBox
from .errors import ConfigError

logger = logging.getLogger(__name__)

LANGUAGES = ("c", "cpp", "csharp", "css", "go", "html", "java", "javascript", "kotlin", "lua",
             "php", "python", "ruby", "rust", "scala", "shell", "sql", "swift", "typescript", "zig")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_WORD_RE = re.compile(r"^[A-Za-z0-9 _.+#:/-]{1,80}$")

_GRACE = timedelta(minutes=2)
STATES_ACTIVE = ("scheduled", "running", "stopping")


class JobBusyError(ValueError):
    """Un travail est déjà en cours ou planifié (le serveur répond 409)."""


class CrawlRequest(BaseModel):
    """Paramètres d'un travail de collecte (validés strictement)."""

    languages: list[str] = Field(default_factory=lambda: ["python"], max_length=10)
    query: str | None = Field(default=None, max_length=80)
    topics: list[str] = Field(default_factory=list, max_length=5)
    repos: list[str] = Field(default_factory=list, max_length=20)
    min_stars: int = Field(default=100, ge=0, le=1_000_000)
    max_repos: int = Field(default=20, ge=1, le=500)
    max_files_per_repo: int = Field(default=15, ge=1, le=200)
    max_files: int = Field(default=200, ge=1, le=20_000)

    # Quand démarrer : maintenant, ou à une heure précise (ISO 8601).
    start_at: str | None = None

    # Quand s'arrêter (au plus tôt des conditions renseignées).
    stop_at: str | None = None
    max_minutes: float | None = Field(default=None, gt=0, le=60 * 24 * 7)
    max_gb: float | None = Field(default=None, gt=0, le=10_000)
    max_library_gb: float | None = Field(default=None, gt=0, le=100_000)
    min_free_gb: float = Field(default=2.0, ge=0.5, le=10_000)

    @field_validator("languages")
    @classmethod
    def _languages(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip().lower() for v in value if v.strip()]
        bad = [v for v in cleaned if v not in LANGUAGES]
        if bad:
            raise ValueError(f"Langage inconnu : {bad[0]}")
        return list(dict.fromkeys(cleaned)) or ["python"]

    @field_validator("topics")
    @classmethod
    def _topics(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v.strip()]
        if any(not _WORD_RE.fullmatch(v) for v in cleaned):
            raise ValueError("Topic invalide.")
        return cleaned

    @field_validator("query")
    @classmethod
    def _query(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not _WORD_RE.fullmatch(value):
            raise ValueError("Mots-clés invalides (lettres, chiffres et - _ . + # : / seulement).")
        return value or None

    @field_validator("repos")
    @classmethod
    def _repos(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v.strip()]
        if any(not _REPO_RE.fullmatch(v) for v in cleaned):
            raise ValueError("Dépôt invalide (format attendu : proprietaire/nom).")
        return cleaned

    @model_validator(mode="after")
    def _times(self) -> "CrawlRequest":
        start = parse_when(self.start_at, "start_at")
        stop = parse_when(self.stop_at, "stop_at")
        if stop is not None:
            reference = start or datetime.now(timezone.utc)
            if stop <= reference:
                raise ValueError("L'heure d'arrêt doit être postérieure au démarrage.")
        return self


def parse_when(value: str | None, name: str = "date") -> datetime | None:
    """Date ISO 8601 -> datetime UTC. Sans fuseau : heure locale du serveur."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} : date invalide (format ISO 8601 attendu).") from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()  # interprétée en heure locale
    return moment.astimezone(timezone.utc)


def build_command(request: CrawlRequest, destination: Path, stop_at: datetime | None) -> list[str]:
    """Ligne de commande du crawler (liste d'arguments : jamais de shell)."""
    command = [sys.executable, "-m", "icc_crawler", "crawl", "--dest", str(destination), "--progress-json"]
    for language in request.languages:
        command += ["--language", language]
    if request.query:
        command += ["--query", request.query]
    for topic in request.topics:
        command += ["--topic", topic]
    for repo in request.repos:
        command += ["--repo", repo]
    command += [
        "--min-stars", str(request.min_stars),
        "--max-repos", str(request.max_repos),
        "--max-files-per-repo", str(request.max_files_per_repo),
        "--max-files", str(request.max_files),
        "--min-free-gb", str(request.min_free_gb),
    ]
    if stop_at is not None:
        command += ["--stop-at", stop_at.isoformat()]
    if request.max_minutes:
        command += ["--max-minutes", str(request.max_minutes)]
    if request.max_gb:
        command += ["--max-gb", str(request.max_gb)]
    if request.max_library_gb:
        command += ["--max-library-gb", str(request.max_library_gb)]
    return command


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat() if moment else None


class CrawlManager:
    """Un seul travail de collecte à la fois, persistant d'un redémarrage à l'autre."""

    def __init__(self, data_dir: Path, watch_root: Path, box: SecretBox | None = None):
        self.data_dir = Path(data_dir)
        self.destination = Path(watch_root) / "github"
        self._box = box
        self._state_file = self.data_dir / "crawler-state.json"
        self._token_file = self.data_dir / "crawler-token.enc"
        self._job: dict | None = None
        self._history: list[dict] = []
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._load()

    # -- jeton GitHub (chiffré, jamais renvoyé) ---------------------------------

    def token_source(self) -> str:
        if self._token_file.exists():
            return "enregistre"
        return "environnement" if os.environ.get("GITHUB_TOKEN") else "aucun"

    def save_token(self, token: str | None, *, clear: bool = False) -> None:
        if clear:
            self._token_file.unlink(missing_ok=True)
            return
        token = (token or "").strip()
        if not token:
            return
        if self._box is None:
            raise ConfigError("Chiffrement indisponible : le jeton n'a pas été enregistré.")
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,255}", token):
            raise ValueError("Format de jeton GitHub invalide.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._token_file.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(self._box.encrypt(token))
        os.replace(temporary, self._token_file)

    def _token(self) -> str | None:
        if self._token_file.exists() and self._box is not None:
            try:
                return self._box.decrypt(self._token_file.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Jeton GitHub enregistré illisible (clé de chiffrement changée ?)")
        return os.environ.get("GITHUB_TOKEN") or None

    # -- état -------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._history = list(data.get("history", []))[-10:]
        job = data.get("job")
        if job and job.get("state") in STATES_ACTIVE:
            if job["state"] == "scheduled":
                self._job = job  # sera réarmé par `resume()`
            else:
                job.update(state="interrupted", ended_at=_iso(datetime.now(timezone.utc)),
                           message="Interrompu par un redémarrage du service.")
                self._history.append(job)
        elif job:
            self._job = job

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {"job": self._job, "history": self._history[-10:]}
            temporary = self._state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, self._state_file)
        except OSError as exc:
            logger.warning("État du crawler non enregistré : %s", exc)

    def status(self) -> dict:
        job = self._job
        return {
            "available": crawler_bridge.ensure_importable(),
            "token": self.token_source(),
            "destination": str(self.destination),
            "active": bool(job and job.get("state") in STATES_ACTIVE),
            "job": job,
            "history": self._history[-5:],
            "disk": self.disk_info(),
        }

    def disk_info(self) -> dict:
        import shutil
        probe = self.destination if self.destination.exists() else self.destination.parent
        try:
            usage = shutil.disk_usage(probe if probe.exists() else Path("/"))
        except OSError:
            return {}
        return {"free_gb": round(usage.free / 1024**3, 2), "total_gb": round(usage.total / 1024**3, 2)}

    # -- cycle de vie ----------------------------------------------------------

    def start(self, request: CrawlRequest) -> dict:
        if self._job and self._job.get("state") in STATES_ACTIVE:
            raise JobBusyError("Un travail de collecte est déjà en cours ou planifié.")
        if not crawler_bridge.ensure_importable():
            raise ConfigError("Le module du crawler est introuvable sur ce serveur.")
        start_at = parse_when(request.start_at, "start_at")
        now = datetime.now(timezone.utc)
        if start_at is not None and start_at < now - _GRACE:
            raise ValueError("L'heure de démarrage est déjà passée.")
        self._job = {
            "id": uuid.uuid4().hex[:12],
            "state": "scheduled",
            "request": request.model_dump(),
            "start_at": _iso(start_at if start_at and start_at > now else None),
            "created_at": _iso(now),
            "started_at": None, "ended_at": None,
            "progress": {}, "stop_reason": None, "message": "", "log": [],
        }
        self._save()
        self._task = asyncio.ensure_future(self._run(self._job["id"]))
        return self._job

    def resume(self) -> None:
        """Réarme un travail planifié avant un redémarrage du service."""
        job = self._job
        if not job or job.get("state") != "scheduled":
            return
        start_at = parse_when(job.get("start_at"))
        if start_at and start_at < datetime.now(timezone.utc) - _GRACE:
            job.update(state="missed", ended_at=_iso(datetime.now(timezone.utc)),
                       message="Heure de démarrage dépassée pendant l'arrêt du service.")
            self._history.append(job)
            self._save()
            return
        self._task = asyncio.ensure_future(self._run(job["id"]))

    async def stop(self) -> dict | None:
        job = self._job
        if not job or job.get("state") not in STATES_ACTIVE:
            return job
        if job["state"] == "scheduled":
            if self._task:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._finish(job, "cancelled", "arret-demande", "Planification annulée.")
            return job
        job["state"] = "stopping"
        self._save()
        if self._process and self._process.returncode is None:
            self._process.send_signal(signal.SIGTERM)
        return job

    async def shutdown(self) -> None:
        """Arrêt du service : demande un arrêt propre et attend un court instant."""
        if self._process and self._process.returncode is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), 10)
            except asyncio.TimeoutError:
                self._process.kill()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _finish(self, job: dict, state: str, reason: str | None, message: str = "") -> None:
        job.update(state=state, stop_reason=reason or job.get("stop_reason"),
                   ended_at=_iso(datetime.now(timezone.utc)))
        if message:
            job["message"] = message
        self._history.append(dict(job))
        self._history = self._history[-10:]
        self._save()

    # -- exécution -----------------------------------------------------------

    async def _run(self, job_id: str) -> None:
        job = self._job
        if not job or job["id"] != job_id:
            return
        try:
            request = CrawlRequest.model_validate(job["request"])
            start_at = parse_when(job.get("start_at"))
            if start_at:
                delay = (start_at - datetime.now(timezone.utc)).total_seconds()
                while delay > 0:
                    await asyncio.sleep(min(delay, 30))
                    delay = (start_at - datetime.now(timezone.utc)).total_seconds()
            stop_at = parse_when(request.stop_at)
            if stop_at and stop_at <= datetime.now(timezone.utc):
                self._finish(job, "finished", "heure-atteinte", "Heure d'arrêt dépassée avant le démarrage.")
                return
            await self._spawn(job, request, stop_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # jamais de plantage silencieux de la tâche
            logger.exception("Échec du travail de collecte")
            self._finish(job, "failed", "erreur", f"Échec : {type(exc).__name__}")

    async def _spawn(self, job: dict, request: CrawlRequest, stop_at: datetime | None) -> None:
        self.destination.mkdir(parents=True, exist_ok=True)
        command = build_command(request, self.destination, stop_at)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.data_dir),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": crawler_bridge.crawler_pythonpath(),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        token = self._token()
        if token:
            environment["GITHUB_TOKEN"] = token

        job.update(state="running", started_at=_iso(datetime.now(timezone.utc)))
        self._save()
        self._process = await asyncio.create_subprocess_exec(
            *command, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL, cwd=str(self.data_dir),
        )
        process = self._process
        if job["state"] == "stopping":
            # Arrêt demandé avant que le processus n'existe : on ne perd pas la demande.
            process.send_signal(signal.SIGTERM)
        final: dict | None = None
        last_save = 0.0

        async def read_stdout() -> None:
            nonlocal final, last_save
            assert process.stdout is not None
            async for raw in process.stdout:
                try:
                    event = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if event.get("event") == "progress":
                    job["progress"] = {k: event.get(k) for k in ("files", "bytes", "repos", "skipped", "elapsed")}
                    if time.monotonic() - last_save > 5:
                        self._save()
                        last_save = time.monotonic()
                elif event.get("event") == "done":
                    final = event

        async def read_stderr() -> None:
            assert process.stderr is not None
            async for raw in process.stderr:
                line = raw.decode("utf-8", "replace").rstrip()[:300]
                if token:
                    line = line.replace(token, "***")
                job["log"] = (job["log"] + [line])[-30:]

        await asyncio.gather(read_stdout(), read_stderr())
        code = await process.wait()
        self._process = None

        if final:
            stats = final.get("stats", {})
            job["progress"] = {
                "files": stats.get("files_saved"), "bytes": stats.get("bytes_written"),
                "repos": stats.get("repos_done"), "skipped": stats.get("files_skipped"),
                "elapsed": stats.get("elapsed_seconds"),
            }
            reason = final.get("stop_reason")
            state = "failed" if code not in (0,) else "finished"
            if job["state"] == "stopping" and reason == "arret-demande":
                state = "stopped"
            self._finish(job, state, reason, REASON_LABELS.get(reason or "", ""))
        else:
            if job["state"] == "stopping":  # tué par notre SIGTERM sans message final : arrêt demandé
                self._finish(job, "stopped", "arret-demande", REASON_LABELS["arret-demande"])
            else:
                self._finish(job, "failed", "erreur", f"Le crawler s'est arrêté (code {code}).")


REASON_LABELS = {
    "termine": "Collecte terminée.",
    "heure-atteinte": "Arrêt : heure limite atteinte.",
    "duree-atteinte": "Arrêt : durée maximale atteinte.",
    "volume-atteint": "Arrêt : volume maximal téléchargé atteint.",
    "bibliotheque-pleine": "Arrêt : taille maximale de la bibliothèque atteinte.",
    "disque-presque-plein": "Arrêt de sécurité : espace disque libre sous le seuil.",
    "arret-demande": "Arrêt demandé.",
    "limite-fichiers": "Arrêt : nombre maximal de fichiers atteint.",
    "limite-de-debit": "Arrêt : limite de débit GitHub (ajoute un jeton).",
    "erreur": "Arrêt sur erreur.",
}
