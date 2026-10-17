# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Bibliothèque de code : import de fichiers avec tags et zone de dépôt.

Deux façons d'ajouter des scripts sans jamais se heurter aux permissions :

1. **Import depuis l'interface** : les fichiers déposés (ou une archive ZIP)
   sont écrits PAR LE SERVICE lui-même dans le dossier surveillé, donc avec
   le bon propriétaire, quel que soit l'utilisateur de ton navigateur ;
2. **Zone de dépôt** `<dossier surveillé>/_depot/` : tu y copies tes fichiers
   (scp, gestionnaire de fichiers…) avec des tags ; le service les range
   puis les retire. Les tags viennent des sous-dossiers, d'un fichier
   `tags.txt` ou d'un fichier annexe `<nom>.tags`.

Les tags deviennent des dossiers imbriqués (`tag1/tag2/<langage>/`) et donc
les catégories de la recherche. Les noms sont assainis (pas de `..`, pas de
chemin absolu, pas de nom caché) et rien n'est jamais écrasé.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import crawler_bridge

logger = logging.getLogger(__name__)

MARKER = ".codecenter-language-dir"
DEPOT_NAME = "_depot"
DEFAULT_CATEGORY = "importes"

MAX_TAGS = 8
MAX_TAG_LENGTH = 40
MAX_FILES_PER_UPLOAD = 200
MAX_ZIP_ENTRIES = 1000


class LibraryError(Exception):
    """Erreur d'import destinée à l'utilisateur (message sans secret)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Limits:
    max_file_bytes: int = 1_000_000
    max_upload_bytes: int = 50_000_000
    min_free_bytes: int = 1_073_741_824  # 1 Gio

    @classmethod
    def from_environment(cls) -> "Limits":
        def number(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "").strip() or default)
            except ValueError:
                return default

        return cls(
            max_file_bytes=int(number("CODECENTER_IMPORT_MAX_FILE_KB", 1000) * 1000),
            max_upload_bytes=int(number("CODECENTER_IMPORT_MAX_UPLOAD_MB", 50) * 1_000_000),
            min_free_bytes=int(number("CODECENTER_MIN_FREE_GB", 1.0) * 1024**3),
        )


@dataclass
class ImportReport:
    saved: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"saved": self.saved, "skipped": self.skipped,
                "saved_count": len(self.saved), "skipped_count": len(self.skipped)}


# --------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------

_TAG_CLEAN = re.compile(r"[^a-z0-9._+-]+")


def slugify_tag(raw: str) -> str | None:
    """Rend un tag utilisable comme nom de dossier (ou None s'il est vide)."""
    text = raw.strip().lower()
    text = (text.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
            .replace("ç", "c").replace("ù", "u").replace("ô", "o").replace("î", "i").replace("â", "a"))
    text = _TAG_CLEAN.sub("-", text).strip("-._")
    text = re.sub(r"\.{2,}", ".", text)[:MAX_TAG_LENGTH].strip("-._")
    return text or None


def parse_tags(raw: str | list[str] | None) -> list[str]:
    """`"API, python  ; Crypto"` -> ["api", "python", "crypto"] (sans doublon, borné)."""
    if raw is None:
        return []
    parts = raw if isinstance(raw, list) else re.split(r"[,;\n]+", raw)
    tags: list[str] = []
    for part in parts:
        tag = slugify_tag(str(part).lstrip("#"))
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def folder_tags(parts, language_slugs) -> list[str]:
    """Tags tirés de noms de dossiers (ZIP, zone de dépôt).

    Un dossier nommé comme un langage (`python/`, `rust/`) est un dossier de
    rangement, pas une catégorie : il est ignoré.
    """
    return [t for t in parse_tags(list(parts)) if t not in language_slugs]


def safe_name(name: str) -> str | None:
    """Nom de fichier sûr (dernier composant, sans caractères dangereux)."""
    base = PurePosixPath(name.replace("\\", "/")).name.strip()
    base = re.sub(r"[^A-Za-z0-9._+-]+", "_", base).strip("._")
    if not base or "." not in base or len(base) > 120:
        return None
    return base


# --------------------------------------------------------------------------
# Bibliothèque
# --------------------------------------------------------------------------

class Library:
    def __init__(self, root: Path, data_dir: Path, limits: Limits | None = None):
        self.root = Path(root)
        self.data_dir = Path(data_dir)
        self.limits = limits or Limits.from_environment()
        self._languages = {}
        self._classify = None
        if crawler_bridge.ensure_importable():
            from icc_crawler import classify as classify_module, filters
            self._languages = {ext: slug for ext, (slug, _name) in filters.LANGUAGES.items()}
            self._classify = classify_module.classify

    # -- environnement ----------------------------------------------------

    @classmethod
    def from_environment(cls, data_dir: Path) -> "Library":
        configured = os.environ.get("CODECENTER_WATCH_PATHS", "")
        first = next((p for p in configured.split(os.pathsep) if p.strip()), "")
        root = Path(first) if first else data_dir / "codes"
        return cls(root, data_dir)

    @property
    def depot(self) -> Path:
        return self.root / DEPOT_NAME

    def ensure_depot(self) -> None:
        """Crée la zone de dépôt et son mode d'emploi (sans échec bloquant)."""
        try:
            self.depot.mkdir(parents=True, exist_ok=True)
            readme = self.depot / "LISEZ-MOI.txt"
            if not readme.exists():
                readme.write_text(DEPOT_README, encoding="utf-8")
        except OSError as exc:
            logger.warning("Zone de dépôt non créée (%s)", exc)

    def language_of(self, filename: str) -> str | None:
        return self._languages.get(PurePosixPath(filename).suffix.lstrip(".").lower())

    def _language_slugs(self) -> set[str]:
        return set(self._languages.values())

    def supported_extensions(self) -> list[str]:
        return sorted(self._languages)

    def _check_disk(self, incoming: int) -> None:
        probe = self.root if self.root.exists() else self.root.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            return
        if free - incoming < self.limits.min_free_bytes:
            raise LibraryError(
                "disk_full",
                f"Espace disque insuffisant : il reste {free // 1024**2} Mo, "
                f"le seuil de sécurité est de {self.limits.min_free_bytes // 1024**2} Mo.",
            )

    # -- import d'un fichier ---------------------------------------------

    def import_bytes(self, name: str, data: bytes, tags: list[str], *, auto_classify: bool = True) -> dict:
        """Range un fichier ; retourne {name, path, tags, language} ou lève LibraryError."""
        filename = safe_name(name)
        if filename is None:
            raise LibraryError("bad_name", "Nom de fichier invalide.")
        language = self.language_of(filename)
        if language is None:
            raise LibraryError("unsupported", "Extension non prise en charge.")
        if not data:
            raise LibraryError("empty", "Fichier vide.")
        if len(data) > self.limits.max_file_bytes:
            raise LibraryError("too_large", f"Fichier trop gros (max {self.limits.max_file_bytes // 1000} Ko).")
        if b"\x00" in data:
            raise LibraryError("binary", "Fichier binaire refusé.")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LibraryError("encoding", "Le fichier doit être encodé en UTF-8.") from exc

        folders = list(tags)
        if not folders:
            category = DEFAULT_CATEGORY
            if auto_classify and self._classify:
                text = data.decode("utf-8-sig", "replace")
                category = self._classify(filename, [], "", text) or DEFAULT_CATEGORY
            folders = [category]
            tags = [category]

        self._check_disk(len(data))
        directory = self.root.joinpath(*folders, language)
        self._confine(directory)
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / MARKER
        if not marker.exists():
            marker.write_text(language, encoding="utf-8")

        digest = hashlib.sha256(data).hexdigest()
        target = directory / filename
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                raise LibraryError("duplicate", "Déjà présent avec le même contenu.")
            stem, dot, ext = filename.rpartition(".")
            target = directory / f"{stem}-{digest[:8]}.{ext}"
            if target.exists():
                raise LibraryError("duplicate", "Déjà présent avec le même contenu.")

        # Écriture atomique : le watcher ne voit jamais un fichier à moitié écrit.
        temporary = directory / f".{target.name}.part"
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)  # jamais de `.part` résiduel
            raise
        return {
            "name": target.name, "language": language, "tags": tags,
            "path": str(target.relative_to(self.root)), "size": len(data),
        }

    def _confine(self, directory: Path) -> None:
        resolved_root = self.root.resolve()
        resolved = directory.resolve(strict=False)
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise LibraryError("outside_root", "Chemin hors de la bibliothèque.")

    # -- import d'un envoi (fichiers et archives) -----------------------------

    def import_upload(self, files: list[tuple[str, bytes]], tags_raw: str | list[str] | None,
                      *, auto_classify: bool = True, keep_folders: bool = True) -> ImportReport:
        if not files:
            raise LibraryError("no_files", "Aucun fichier reçu.")
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise LibraryError("too_many", f"Trop de fichiers (max {MAX_FILES_PER_UPLOAD}).")
        if sum(len(data) for _, data in files) > self.limits.max_upload_bytes:
            raise LibraryError("upload_too_large",
                               f"Envoi trop volumineux (max {self.limits.max_upload_bytes // 1_000_000} Mo).")
        tags = parse_tags(tags_raw)
        report = ImportReport()

        for name, data in files:
            if name.lower().endswith(".zip"):
                self._import_zip(name, data, tags, auto_classify, keep_folders, report)
                continue
            self._import_one(name, data, tags, auto_classify, report)
        return report

    def _import_one(self, name, data, tags, auto_classify, report: ImportReport) -> None:
        try:
            report.saved.append(self.import_bytes(name, data, tags, auto_classify=auto_classify))
        except LibraryError as exc:
            report.skipped.append({"name": PurePosixPath(name.replace("\\", "/")).name,
                                   "reason": exc.message, "code": exc.code})
        except OSError as exc:
            logger.warning("Écriture impossible pour %s : %s", name, exc)
            report.skipped.append({"name": name, "reason": "Écriture impossible (permissions du dossier surveillé ?).",
                                   "code": "io"})

    def _import_zip(self, name, data, tags, auto_classify, keep_folders, report: ImportReport) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            report.skipped.append({"name": name, "reason": "Archive ZIP illisible.", "code": "bad_zip"})
            return
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            report.skipped.append({"name": name, "reason": f"Archive trop riche (max {MAX_ZIP_ENTRIES} entrées).",
                                   "code": "zip_too_many"})
            return
        budget = self.limits.max_upload_bytes
        for info in entries:
            if info.is_dir():
                continue
            pure = PurePosixPath(info.filename.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                report.skipped.append({"name": info.filename, "reason": "Chemin dangereux dans l'archive.",
                                       "code": "zip_slip"})
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # lien symbolique
                report.skipped.append({"name": info.filename, "reason": "Lien symbolique ignoré.", "code": "zip_link"})
                continue
            if info.file_size > self.limits.max_file_bytes or info.file_size > budget:
                report.skipped.append({"name": info.filename, "reason": "Fichier trop gros.", "code": "too_large"})
                continue
            if self.language_of(pure.name) is None or any(part.startswith(".") or part == "__MACOSX"
                                                          for part in pure.parts):
                continue  # extension non gérée ou fichier caché : ignoré en silence
            try:
                with archive.open(info) as handle:
                    content = handle.read(self.limits.max_file_bytes + 1)  # borne réelle, pas l'en-tête
            except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError):
                # En-tête mensonger, CRC faux, entrée chiffrée : on rapporte, on ne plante pas.
                report.skipped.append({"name": pure.name, "reason": "Entrée d'archive illisible ou chiffrée.",
                                       "code": "zip_corrupt"})
                continue
            budget -= len(content)
            extra = folder_tags(pure.parts[:-1], self._language_slugs()) if keep_folders else []
            merged = (tags + [t for t in extra if t not in tags])[:MAX_TAGS]
            self._import_one(pure.name, content, merged, auto_classify, report)

    # -- zone de dépôt --------------------------------------------------------

    def _ledger_path(self) -> Path:
        return self.data_dir / "depot-ledger.json"

    def _load_ledger(self) -> dict:
        try:
            return json.loads(self._ledger_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_ledger(self, ledger: dict) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._ledger_path().with_suffix(".tmp")
            temporary.write_text(json.dumps(ledger), encoding="utf-8")
            os.replace(temporary, self._ledger_path())
        except OSError as exc:
            logger.warning("Registre du dépôt non écrit : %s", exc)

    @staticmethod
    def _read_tags_file(path: Path) -> list[str]:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return parse_tags(line)
        except OSError:
            pass
        return []

    def scan_depot(self, *, settle_seconds: float = 3.0) -> ImportReport:
        """Range les fichiers de la zone de dépôt ; sans effet si elle est vide."""
        report = ImportReport()
        if not self.depot.is_dir():
            return report
        ledger = self._load_ledger()
        changed = False
        now = time.time()

        for path in sorted(self.depot.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.depot)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.name.lower() in ("tags.txt", "lisez-moi.txt") or path.suffix == ".tags":
                continue
            if self.language_of(path.name) is None:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime < settle_seconds:
                continue  # copie probablement en cours
            key = f"{relative.as_posix()}|{stat.st_size}|{int(stat.st_mtime)}"
            if key in ledger:
                continue

            tags = self._depot_tags(path, relative)
            try:
                data = path.read_bytes()
            except OSError as exc:
                report.skipped.append({"name": relative.as_posix(), "reason": f"Lecture impossible : {exc.strerror}",
                                       "code": "io"})
                continue
            try:
                saved = self.import_bytes(path.name, data, tags)
                report.saved.append(saved)
            except LibraryError as exc:
                report.skipped.append({"name": relative.as_posix(), "reason": exc.message, "code": exc.code})
                if exc.code == "disk_full":
                    break
            except OSError as exc:
                report.skipped.append({"name": relative.as_posix(), "reason": f"Écriture impossible : {exc.strerror}",
                                       "code": "io"})
                continue

            # Source retirée si possible ; sinon mémorisée pour ne pas la retraiter.
            try:
                path.unlink()
                sidecar = path.with_name(path.name + ".tags")
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                ledger[key] = int(now)
                changed = True

        if changed:
            self._save_ledger(ledger)
        return report

    def _depot_tags(self, path: Path, relative: Path) -> list[str]:
        tags: list[str] = []
        sidecar = path.with_name(path.name + ".tags")
        if sidecar.is_file():
            tags = self._read_tags_file(sidecar)
        else:
            directory = path.parent
            while directory != self.depot.parent:
                candidate = directory / "tags.txt"
                if candidate.is_file():
                    tags = self._read_tags_file(candidate)
                    break
                if directory == self.depot:
                    break
                directory = directory.parent
        for folder in folder_tags(relative.parts[:-1], self._language_slugs()):
            if folder not in tags:
                tags.append(folder)
        return tags[:MAX_TAGS]


DEPOT_README = """ZONE DE DÉPÔT — Imperial CodeCenter
====================================

Copie ici tes scripts : ils sont rangés automatiquement puis retirés.

Comment donner des tags ?
  1. Fichier `tags.txt` dans ce dossier (ou un sous-dossier) :
         une ligne, par exemple :  api, python, chiffrement
  2. Fichier annexe `<nom du script>.tags` à côté du script.
  3. Sous-dossiers : `_depot/crypto/aes.py` reçoit le tag « crypto ».

Les tags deviennent des dossiers (tag1/tag2/<langage>/) donc des catégories
de recherche. Sans tag, le script est classé automatiquement.

Les fichiers sont traités quelques secondes après la fin de leur copie.
Ce dossier est surveillé par le service : aucun chmod n'est nécessaire,
mais il doit être inscriptible par ton compte (voir docs/permissions.md).
"""
