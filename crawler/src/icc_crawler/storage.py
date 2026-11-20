# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Stockage : fichiers rangés dans le dossier surveillé + base de suivi SQLite.

Le dossier surveillé ne reçoit que du code (`<dest>/<catégorie>[/<sous-catégorie>]/<fichier>`),
que le watcher de CodeCenter range ensuite par langage. Tout le reste
(état de reprise, manifeste de provenance, textes de licence) vit dans
`<dest>/.crawler/`, avec des extensions que l'indexeur ignore.

Suivi (schéma v2) : un fichier est identifié par (dépôt, chemin). La base retient
la branche, le commit, le SHA git du blob (pour ne pas retélécharger un fichier
inchangé) et le SHA-256 du contenu. Un fichier modifié sur GitHub est mis à jour
sur place ; l'ancienne version locale est conservée dans `file_versions`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 2
KEEP_VERSIONS = 20          # versions locales conservées par fichier (historique)

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    full_name TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    license TEXT,
    stars INTEGER,
    status TEXT NOT NULL,
    files_saved INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    quota INTEGER,
    config_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL,
    blob_sha TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL,
    local_sha256 TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license TEXT,
    language TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    classified_by TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'active',
    size INTEGER NOT NULL,
    dest_path TEXT NOT NULL,
    collected_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    edited_at REAL,
    UNIQUE (repo, path)
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repo);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category, subcategory);
CREATE TABLE IF NOT EXISTS file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    content BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_file ON file_versions(file_id, id);
"""

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DOTS = re.compile(r"\.{2,}")
MAX_NAME = 120
MAX_EXT = 16


def safe_filename(owner_repo: str, path: str, taken: set[str] | None = None) -> str:
    """Nom plat et sûr : `owner__repo__chemin-aplati.ext`, unique et borné.

    Aucun séparateur de chemin, aucun `..`, aucun caractère de contrôle, jamais de
    nom caché (commence toujours par une lettre ou un chiffre), longueur bornée.
    `taken` (comparaison insensible à la casse) reçoit le nom retenu.
    """
    pure = PurePosixPath(path)
    ext = _DOTS.sub(".", _SAFE.sub("", pure.suffix))[:MAX_EXT]
    if ext == ".":
        ext = ""
    stem_parts = [*pure.parts[:-1], pure.stem] if pure.parts else []
    flat = "__".join([owner_repo.replace("/", "__"), *stem_parts])
    flat = _DOTS.sub(".", _SAFE.sub("-", flat)).strip("-._") or "fichier"

    if len(flat) + len(ext) > MAX_NAME:
        digest = hashlib.sha1(f"{owner_repo}/{path}".encode()).hexdigest()[:8]
        flat = f"{flat[: MAX_NAME - len(ext) - 9].rstrip('-._')}-{digest}"

    name = f"{flat}{ext}"
    if taken is not None:
        folded = {t.lower() for t in taken}
        counter = 2
        while name.lower() in folded:
            name = f"{flat}-{counter}{ext}"
            counter += 1
        taken.add(name)
    return name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_regular_file(path: Path) -> bool:
    """Fichier ordinaire (ni lien symbolique, ni périphérique) : jamais suivi hors de la bibliothèque."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


class Store:
    def __init__(self, destination: Path):
        self.destination = Path(destination)
        self.meta = self.destination / ".crawler"
        self.meta.mkdir(parents=True, exist_ok=True)
        (self.meta / "licenses").mkdir(exist_ok=True)
        # `timeout` : le crawler (sous-processus) et l'API partagent ce fichier.
        self._db = sqlite3.connect(self.meta / "crawler.sqlite3", timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self._names_cache: set[str] | None = None
        # Résultat de la dernière écriture : new | updated | restored | unchanged | duplicate | supprime.
        self.last_action = ""

    @property
    def db(self) -> sqlite3.Connection:
        return self._db

    @property
    def _names(self) -> set[str]:
        """Noms déjà attribués (même si le moteur a déplacé ou renommé les fichiers). Chargés à la demande."""
        if self._names_cache is None:
            self._names_cache = {
                PurePosixPath(row["dest_path"]).name.lower()
                for row in self._db.execute("SELECT dest_path FROM files")
            }
        return self._names_cache

    def close(self) -> None:
        self._db.close()

    # -- schéma -----------------------------------------------------------------

    def _migrate(self) -> None:
        """Crée le schéma v2, ou migre une base v1 (`files` indexée par sha256) sans perdre de ligne."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(files)")}
        if columns and "id" not in columns:       # base v1
            self._migrate_from_v1()
        else:
            self._db.executescript(SCHEMA)
        repo_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(repos)")}
        for column, definition in (("branch", "TEXT NOT NULL DEFAULT ''"), ("quota", "INTEGER"),
                                   ("config_key", "TEXT NOT NULL DEFAULT ''")):
            if column not in repo_columns:
                with self._db:
                    self._db.execute(f"ALTER TABLE repos ADD COLUMN {column} {definition}")
        if version != SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate_from_v1(self) -> None:
        with self._db:
            self._db.execute("ALTER TABLE files RENAME TO files_v1")
            self._db.execute("DROP INDEX IF EXISTS idx_files_repo")
            self._db.executescript(SCHEMA)
            # Un même (dépôt, chemin) pouvait exister en plusieurs versions : on garde la plus récente.
            self._db.execute(
                """
                INSERT OR IGNORE INTO files (repo, path, branch, commit_sha, blob_sha, sha256, local_sha256,
                    source_url, license, language, category, subcategory, tags, classified_by, status,
                    size, dest_path, collected_at, updated_at)
                SELECT repo, path, '', commit_sha, '', sha256, sha256, source_url, license, language,
                       category, '', json_array(category), 'auto', 'active', size, dest_path, saved_at, saved_at
                FROM files_v1 ORDER BY saved_at DESC
                """
            )
            self._db.execute("DROP TABLE files_v1")

    # -- reprise --------------------------------------------------------------

    def repo_done(self, full_name: str, commit_sha: str, *, quota: int | None = None, key: str = "") -> bool:
        """Ce dépôt n'a-t-il rien de plus à donner à ce commit, avec ces réglages ?

        `status = done` : exploré en entier. `status = quota` : l'exploration avait été coupée par le quota
        de fichiers par dépôt ; elle n'est « terminée » que si le quota demandé maintenant n'est pas plus
        haut (sans quota, ou avec un quota plus haut, il faut explorer la suite). Une empreinte `key`
        différente (autres filtres de fichiers) impose aussi de réexplorer.
        """
        row = self._db.execute(
            "SELECT status, commit_sha, quota, config_key FROM repos WHERE full_name = ?", (full_name,)
        ).fetchone()
        if not row or row["commit_sha"] != commit_sha:
            return False
        if key and row["config_key"] and row["config_key"] != key:
            return False
        if row["status"] == "done":
            return True
        if row["status"] == "quota":
            return quota is not None and row["quota"] is not None and quota <= row["quota"]
        return False

    def mark_repo(self, full_name: str, commit_sha: str, license_id: str | None,
                  stars: int, status: str, files_saved: int, branch: str = "",
                  quota: int | None = None, key: str = "") -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO repos (full_name, commit_sha, license, stars, status, files_saved, "
                "updated_at, branch, quota, config_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (full_name, commit_sha, license_id, stars, status, files_saved, time.time(), branch, quota, key),
            )

    def forget_repo(self, full_name: str) -> None:
        """Oublie l'état « terminé » d'un dépôt : il sera réexploré en entier (collecte forcée)."""
        with self._db:
            self._db.execute("DELETE FROM repos WHERE full_name = ?", (full_name,))

    # -- fichiers -------------------------------------------------------------
    #
    # Le suivi repose UNIQUEMENT sur la base SQLite (dépôt, chemin, commit, sha256) : le moteur
    # d'indexation déplace les fichiers, leur présence à l'emplacement d'origine n'est jamais requise.

    def known_hash(self, sha256: str) -> bool:
        return self._db.execute("SELECT 1 FROM files WHERE sha256 = ?", (sha256,)).fetchone() is not None

    def get_file(self, repo: str, path: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM files WHERE repo = ? AND path = ?", (repo, path)).fetchone()

    def has_path(self, repo: str, commit_sha: str, path: str) -> bool:
        """Ce chemin de ce dépôt à ce commit a-t-il déjà été enregistré ?"""
        return self._db.execute(
            "SELECT 1 FROM files WHERE repo = ? AND commit_sha = ? AND path = ? AND status = 'active'",
            (repo, commit_sha, path),
        ).fetchone() is not None

    def library_size(self) -> int:
        """Octets de code enregistrés selon la base (indépendant de l'emplacement actuel)."""
        return int(self._db.execute(
            "SELECT COALESCE(SUM(size), 0) FROM files WHERE status = 'active'").fetchone()[0])

    def touch(self, file_id: int, *, commit_sha: str, branch: str, blob_sha: str) -> None:
        """Fichier inchangé sur GitHub : met seulement à jour sa provenance (commit, branche)."""
        with self._db:
            self._db.execute(
                "UPDATE files SET commit_sha = ?, branch = ?, blob_sha = CASE WHEN ? != '' THEN ? ELSE blob_sha END, "
                "updated_at = ? WHERE id = ?",
                (commit_sha, branch, blob_sha, blob_sha, time.time(), file_id),
            )

    # -- emplacement local -----------------------------------------------------

    def resolve_local(self, row: sqlite3.Row) -> Path | None:
        """Retrouve le fichier sur le disque, même si le moteur l'a déplacé dans un dossier de langage.

        Retourne None s'il est introuvable. Seuls des fichiers ordinaires situés sous le dossier
        de destination sont acceptés (jamais un lien symbolique ni un chemin extérieur).
        """
        root = self.destination.resolve()
        recorded = PurePosixPath(row["dest_path"])
        sha8 = row["sha256"][:8]
        candidates = [
            recorded,
            recorded.parent / row["language"] / recorded.name,
            recorded.parent / row["language"] / f"{recorded.stem}-{sha8}{recorded.suffix}",
        ]
        for relative in candidates:
            found = self._accept(root, relative)
            if found is not None:
                self._remember_location(row["id"], found)
                return found

        # Dernier recours : même nom de fichier n'importe où sous la bibliothèque (hors `.crawler`).
        wanted = {recorded.name.lower(), f"{recorded.stem}-{sha8}{recorded.suffix}".lower()}
        for current, directories, names in os.walk(self.destination):
            directories[:] = [d for d in directories if d != ".crawler"]
            for name in names:
                if name.lower() in wanted:
                    found = self._accept(root, (Path(current) / name).relative_to(self.destination).as_posix())
                    if found is not None:
                        self._remember_location(row["id"], found)
                        return found
        return None

    def _accept(self, root: Path, relative) -> Path | None:
        candidate = self.destination / Path(str(relative))
        if not is_regular_file(candidate):
            return None
        try:
            if not candidate.resolve().is_relative_to(root):
                return None
        except OSError:
            return None
        return candidate

    def _remember_location(self, file_id: int, found: Path) -> None:
        relative = found.relative_to(self.destination).as_posix()
        with self._db:
            self._db.execute("UPDATE files SET dest_path = ? WHERE id = ? AND dest_path != ?",
                             (relative, file_id, relative))

    # -- écriture ----------------------------------------------------------------

    def _pick_name(self, folder: Path, repo: str, path: str, digest: str) -> tuple[str, bool]:
        """Nom libre dans `folder` ; (nom, déjà-présent-et-identique). Reprend un orphelin identique."""
        base = safe_filename(repo, path)
        stem, ext = os.path.splitext(base)
        name, counter = base, 2
        while True:
            occupied = name.lower() in self._names
            candidate = folder / name
            if not occupied and candidate.is_file() and not candidate.is_symlink():
                # Fichier orphelin d'une exécution interrompue : on le reprend s'il est identique.
                if _sha256(candidate.read_bytes()) == digest:
                    return name, True
                occupied = True
            elif not occupied and candidate.exists():
                occupied = True
            if not occupied:
                return name, False
            name = f"{stem}-{counter}{ext}"
            counter += 1

    def _category_folder(self, category: str, subcategory: str) -> Path:
        """Dossier `<dest>/<catégorie>[/<sous-catégorie>]`, confiné au dossier de destination."""
        parts = [category, subcategory] if subcategory else [category]
        folder = self.destination.joinpath(*parts)
        root = self.destination.resolve()
        # `Path.resolve` suit les liens : un lien symbolique dans l'arbre est détecté ici.
        if any(not part or part in (".", "..") or "/" in part or "\\" in part for part in parts) \
                or not folder.resolve().is_relative_to(root):
            raise ValueError(f"Catégorie hors du dossier de destination : {'/'.join(parts)!r}")
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.resolve().is_relative_to(root):
            raise ValueError(f"Catégorie hors du dossier de destination : {'/'.join(parts)!r}")
        return folder

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        """Écriture atomique : le watcher ne doit jamais lire un fichier à moitié écrit."""
        temporary = target.parent / f".{target.name}.part"
        temporary.unlink(missing_ok=True)
        try:
            with open(temporary, "xb") as handle:
                handle.write(data)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def save_file(self, *, repo: str, commit_sha: str, path: str, license_id: str | None,
                  language: str, category: str, data: bytes, subcategory: str = "",
                  tags: tuple[str, ...] | list[str] = (), branch: str = "", blob_sha: str = "",
                  classified_by: str = "auto", force: bool = False) -> tuple[Path | None, int]:
        """Enregistre ou met à jour un fichier ; les octets d'origine ne sont jamais modifiés.

        Retourne (chemin, octets écrits) ; (None, 0) si rien n'a été écrit. `last_action` dit pourquoi :
        `new`, `updated` (contenu modifié sur GitHub), `restored` (fichier local manquant),
        `unchanged`, `duplicate` (même contenu déjà connu ailleurs) ou `supprime` (supprimé localement).
        """
        digest = _sha256(data)
        row = self.get_file(repo, path)
        meta = dict(repo=repo, commit_sha=commit_sha, path=path, license_id=license_id, language=language,
                    category=category, subcategory=subcategory, tags=tuple(tags), branch=branch,
                    blob_sha=blob_sha, classified_by=classified_by)

        if row is not None:
            if row["status"] == "deleted":
                if not force:
                    self.last_action = "supprime"
                    return None, 0
                return self._write_new(data, digest, row=row, **meta)
            if digest == row["sha256"]:
                return self._unchanged(row, data, commit_sha, branch, blob_sha, force)
            return self._replace(row, data, digest, **meta)

        if self.known_hash(digest):
            self.last_action = "duplicate"
            return None, 0
        return self._write_new(data, digest, row=None, **meta)

    def _unchanged(self, row, data: bytes, commit_sha: str, branch: str, blob_sha: str, force: bool):
        """Contenu identique à l'original : provenance mise à jour ; fichier local rétabli s'il manque."""
        local = self.resolve_local(row)
        if local is None:
            target, relative = self._free_path(row, row["sha256"])
            self._atomic_write(target, data)
            with self._db:
                self._db.execute("UPDATE files SET dest_path = ?, local_sha256 = ?, size = ?, edited_at = NULL "
                                 "WHERE id = ?", (relative, row["sha256"], len(data), row["id"]))
            self.touch(row["id"], commit_sha=commit_sha, branch=branch, blob_sha=blob_sha)
            self.last_action = "restored"
            return self.destination / relative, len(data)
        self.touch(row["id"], commit_sha=commit_sha, branch=branch, blob_sha=blob_sha)
        self.last_action = "unchanged"
        return None, 0

    def _free_path(self, row, digest: str) -> tuple[Path, str]:
        """Nouvel emplacement libre (chemin, chemin relatif) dans le dossier de catégorie d'une ligne existante."""
        folder = self._category_folder(row["category"], row["subcategory"])
        name, already = self._pick_name(folder, row["repo"], row["path"], digest)
        target = folder / name
        if not target.resolve().is_relative_to(self.destination.resolve()):
            raise ValueError(f"Chemin hors du dossier de destination : {target}")
        self._names.add(name.lower())
        return target, target.relative_to(self.destination).as_posix()

    def _write_new(self, data: bytes, digest: str, *, row, repo: str, commit_sha: str, path: str,
                   license_id, language: str, category: str, subcategory: str, tags, branch: str,
                   blob_sha: str, classified_by: str):
        folder = self._category_folder(category, subcategory)
        name, already = self._pick_name(folder, repo, path, digest)
        target = folder / name
        if not target.resolve().is_relative_to(self.destination.resolve()):
            raise ValueError(f"Chemin hors du dossier de destination : {target}")

        written = 0
        if not already:
            self._atomic_write(target, data)
            written = len(data)

        now = time.time()
        relative = target.relative_to(self.destination).as_posix()
        url = f"https://github.com/{repo}/blob/{commit_sha}/{path}"
        try:
            with self._db:
                if row is None:
                    self._db.execute(
                        "INSERT INTO files (repo, path, branch, commit_sha, blob_sha, sha256, local_sha256, "
                        "source_url, license, language, category, subcategory, tags, classified_by, status, "
                        "size, dest_path, collected_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                        (repo, path, branch, commit_sha, blob_sha, digest, digest, url, license_id, language,
                         category, subcategory, json.dumps(list(tags)), classified_by, len(data), relative,
                         now, now),
                    )
                else:   # collecte forcée d'un fichier supprimé : la ligne reprend vie
                    self._db.execute(
                        "UPDATE files SET branch = ?, commit_sha = ?, blob_sha = ?, sha256 = ?, local_sha256 = ?, "
                        "source_url = ?, license = ?, language = ?, category = ?, subcategory = ?, tags = ?, "
                        "classified_by = ?, status = 'active', size = ?, dest_path = ?, updated_at = ?, "
                        "edited_at = NULL WHERE id = ?",
                        (branch, commit_sha, blob_sha, digest, digest, url, license_id, language, category,
                         subcategory, json.dumps(list(tags)), classified_by, len(data), relative, now, row["id"]),
                    )
        except BaseException:
            if written:
                target.unlink(missing_ok=True)
            raise
        self._names.add(name.lower())
        self.last_action = "new"
        return target, written

    def _replace(self, row, data: bytes, digest: str, *, repo: str, commit_sha: str, path: str, license_id,
                 language: str, category: str, subcategory: str, tags, branch: str, blob_sha: str,
                 classified_by: str):
        """Contenu modifié sur GitHub : mise à jour sur place, ancienne version locale conservée."""
        local = self.resolve_local(row)
        previous = local.read_bytes() if local is not None else None
        if local is None:
            local, relative = self._free_path(row, digest)
        else:
            relative = local.relative_to(self.destination).as_posix()

        self._atomic_write(local, data)
        now = time.time()
        url = f"https://github.com/{repo}/blob/{commit_sha}/{path}"
        try:
            with self._db:
                if previous is not None and _sha256(previous) != digest:
                    self.add_version(row["id"], previous, "mise-a-jour-github", commit=False)
                self._db.execute(
                    "UPDATE files SET branch = ?, commit_sha = ?, blob_sha = ?, sha256 = ?, local_sha256 = ?, "
                    "source_url = ?, license = ?, size = ?, dest_path = ?, updated_at = ?, edited_at = NULL "
                    "WHERE id = ?",
                    (branch, commit_sha, blob_sha, digest, digest, url, license_id, len(data), relative, now,
                     row["id"]),
                )
        except BaseException:
            if previous is not None:      # rétablit le contenu local d'avant
                self._atomic_write(local, previous)
            raise
        self.last_action = "updated"
        return local, len(data)

    # -- historique ------------------------------------------------------------

    def add_version(self, file_id: int, content: bytes, reason: str, *, commit: bool = True) -> None:
        """Conserve une version locale (les plus anciennes au-delà de KEEP_VERSIONS sont retirées)."""
        def insert() -> None:
            self._db.execute(
                "INSERT INTO file_versions (file_id, sha256, size, reason, created_at, content) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, _sha256(content), len(content), reason, time.time(), content),
            )
            self._db.execute(
                "DELETE FROM file_versions WHERE file_id = ? AND id NOT IN "
                "(SELECT id FROM file_versions WHERE file_id = ? ORDER BY id DESC LIMIT ?)",
                (file_id, file_id, KEEP_VERSIONS),
            )

        if commit:
            with self._db:
                insert()
        else:
            insert()

    def save_license(self, repo: str, text: str | None, license_id: str | None) -> None:
        """Conserve le texte de licence (obligation d'attribution), hors indexation."""
        stem = _DOTS.sub(".", _SAFE.sub("-", repo.replace("/", "__"))).strip("-._") or "depot"
        target = self.meta / "licenses" / f"{stem}.txt"
        body = text or f"Licence déclarée : {license_id or 'inconnue'} (texte non récupéré)."
        target.write_text(body, encoding="utf-8")

    # -- lecture --------------------------------------------------------------

    def stats(self) -> dict:
        row = self._db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS size FROM files WHERE status = 'active'").fetchone()
        by_category = {
            r["category"]: r["n"] for r in self._db.execute(
                "SELECT category, COUNT(*) AS n FROM files WHERE status = 'active' "
                "GROUP BY category ORDER BY n DESC, category")
        }
        by_language = {
            r["language"]: r["n"] for r in self._db.execute(
                "SELECT language, COUNT(*) AS n FROM files WHERE status = 'active' "
                "GROUP BY language ORDER BY n DESC, language")
        }
        repos = self._db.execute(
            "SELECT COUNT(*) AS n FROM repos WHERE status IN ('done', 'quota')").fetchone()["n"]
        return {"files": row["n"], "bytes": row["size"], "repos": repos,
                "by_category": by_category, "by_language": by_language}

    def export_manifest(self, output: Path) -> int:
        """Écrit le manifeste de provenance au format JSON Lines."""
        rows = self._db.execute("SELECT * FROM files WHERE status = 'active' ORDER BY repo, path").fetchall()
        with Path(output).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        return len(rows)
