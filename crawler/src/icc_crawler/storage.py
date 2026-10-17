# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Stockage : fichiers rangés dans le dossier surveillé + base de suivi SQLite.

Le dossier surveillé ne reçoit que du code (`<dest>/<catégorie>/<fichier>`),
que le watcher de CodeCenter range ensuite par langage. Tout le reste
(état de reprise, manifeste de provenance, textes de licence) vit dans
`<dest>/.crawler/`, avec des extensions que l'indexeur ignore.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path, PurePosixPath

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    full_name TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    license TEXT,
    stars INTEGER,
    status TEXT NOT NULL,
    files_saved INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    sha256 TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license TEXT,
    language TEXT NOT NULL,
    category TEXT NOT NULL,
    size INTEGER NOT NULL,
    dest_path TEXT NOT NULL,
    saved_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repo);
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


class Store:
    def __init__(self, destination: Path):
        self.destination = Path(destination)
        self.meta = self.destination / ".crawler"
        self.meta.mkdir(parents=True, exist_ok=True)
        (self.meta / "licenses").mkdir(exist_ok=True)
        self._db = sqlite3.connect(self.meta / "crawler.sqlite3")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        # Noms déjà attribués (même si le moteur a déplacé ou renommé les fichiers).
        self._names: set[str] = {
            PurePosixPath(row["dest_path"]).name.lower()
            for row in self._db.execute("SELECT dest_path FROM files")
        }

    def close(self) -> None:
        self._db.close()

    # -- reprise --------------------------------------------------------------

    def repo_done(self, full_name: str, commit_sha: str) -> bool:
        row = self._db.execute(
            "SELECT status, commit_sha FROM repos WHERE full_name = ?", (full_name,)
        ).fetchone()
        return bool(row and row["status"] == "done" and row["commit_sha"] == commit_sha)

    def mark_repo(self, full_name: str, commit_sha: str, license_id: str | None,
                  stars: int, status: str, files_saved: int) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO repos VALUES (?, ?, ?, ?, ?, ?, ?)",
                (full_name, commit_sha, license_id, stars, status, files_saved, time.time()),
            )

    # -- fichiers -------------------------------------------------------------
    #
    # La déduplication repose UNIQUEMENT sur la base SQLite (sha256, dépôt, commit,
    # chemin) : le moteur d'indexation déplace les fichiers, leur présence à
    # l'emplacement d'origine n'est jamais requise.

    def known_hash(self, sha256: str) -> bool:
        return self._db.execute("SELECT 1 FROM files WHERE sha256 = ?", (sha256,)).fetchone() is not None

    def has_path(self, repo: str, commit_sha: str, path: str) -> bool:
        """Ce chemin de ce dépôt à ce commit a-t-il déjà été enregistré ?"""
        return self._db.execute(
            "SELECT 1 FROM files WHERE repo = ? AND commit_sha = ? AND path = ?", (repo, commit_sha, path)
        ).fetchone() is not None

    def library_size(self) -> int:
        """Octets de code enregistrés selon la base (indépendant de l'emplacement actuel)."""
        return int(self._db.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0])

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
                if hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                    return name, True
                occupied = True
            elif not occupied and candidate.exists():
                occupied = True
            if not occupied:
                return name, False
            name = f"{stem}-{counter}{ext}"
            counter += 1

    def save_file(self, *, repo: str, commit_sha: str, path: str, license_id: str | None,
                  language: str, category: str, data: bytes) -> tuple[Path | None, int]:
        """Écrit les octets d'origine (jamais modifiés) et les référence.

        Retourne (chemin, octets réellement écrits) ; (None, 0) si le contenu est déjà connu.
        """
        digest = hashlib.sha256(data).hexdigest()
        if self.known_hash(digest):
            return None, 0

        folder = (self.destination / category)
        folder.mkdir(parents=True, exist_ok=True)
        root = self.destination.resolve()
        if not folder.resolve().is_relative_to(root):
            raise ValueError(f"Catégorie hors du dossier de destination : {category!r}")

        name, already = self._pick_name(folder, repo, path, digest)
        target = folder / name
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"Chemin hors du dossier de destination : {target}")

        written = 0
        if not already:
            # Écriture atomique : le watcher ne doit jamais lire un fichier à moitié écrit.
            temporary = folder / f".{name}.part"
            temporary.unlink(missing_ok=True)
            try:
                with open(temporary, "xb") as handle:
                    handle.write(data)
                os.replace(temporary, target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            written = len(data)

        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (digest, repo, commit_sha, path,
                     f"https://github.com/{repo}/blob/{commit_sha}/{path}",
                     license_id, language, category, len(data),
                     str(target.relative_to(self.destination)), time.time()),
                )
        except BaseException:
            if written:
                target.unlink(missing_ok=True)
            raise
        self._names.add(name.lower())
        return target, written

    def save_license(self, repo: str, text: str | None, license_id: str | None) -> None:
        """Conserve le texte de licence (obligation d'attribution), hors indexation."""
        stem = _DOTS.sub(".", _SAFE.sub("-", repo.replace("/", "__"))).strip("-._") or "depot"
        target = self.meta / "licenses" / f"{stem}.txt"
        body = text or f"Licence déclarée : {license_id or 'inconnue'} (texte non récupéré)."
        target.write_text(body, encoding="utf-8")

    # -- lecture --------------------------------------------------------------

    def stats(self) -> dict:
        row = self._db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS size FROM files").fetchone()
        by_category = {
            r["category"]: r["n"] for r in self._db.execute(
                "SELECT category, COUNT(*) AS n FROM files GROUP BY category ORDER BY n DESC")
        }
        by_language = {
            r["language"]: r["n"] for r in self._db.execute(
                "SELECT language, COUNT(*) AS n FROM files GROUP BY language ORDER BY n DESC")
        }
        repos = self._db.execute("SELECT COUNT(*) AS n FROM repos WHERE status = 'done'").fetchone()["n"]
        return {"files": row["n"], "bytes": row["size"], "repos": repos,
                "by_category": by_category, "by_language": by_language}

    def export_manifest(self, output: Path) -> int:
        """Écrit le manifeste de provenance au format JSON Lines."""
        rows = self._db.execute("SELECT * FROM files ORDER BY repo, path").fetchall()
        with Path(output).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        return len(rows)
