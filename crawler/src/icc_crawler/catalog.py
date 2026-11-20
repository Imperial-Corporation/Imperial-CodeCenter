# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Bibliothèque locale : consulter, modifier, reclasser et supprimer les fichiers collectés.

Tout se passe dans le dossier de destination du crawler et dans sa base SQLite : rien n'est
jamais envoyé vers GitHub. Les modifications locales gardent un historique (restaurable) ;
un fichier supprimé laisse une « pierre tombale » pour qu'une collecte suivante ne le
retélécharge pas (sauf collecte forcée).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .classify import DEFAULT_CATEGORY, is_valid_folder_name, slugify_part
from .storage import Store, is_regular_file

MARKER = ".codecenter-language-dir"          # même marqueur que le moteur et l'import
MAX_VIEW_BYTES = 5 * 1024 * 1024             # au-delà : lecture par tranches seulement
MAX_EDIT_BYTES = 2 * 1024 * 1024             # taille maximale d'un contenu enregistré localement
MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024        # recherche dans le contenu : fichiers plus gros ignorés
SCAN_FILE_LIMIT = 20_000                     # recherche dans le contenu : nombre de fichiers lus au plus
SCAN_SECONDS = 8.0                           # … et durée maximale
MAX_PAGE_SIZE = 200
MAX_CHUNK_BYTES = 1024 * 1024                # tranche maximale renvoyée en une lecture
CLASSIFIED_BY = ("auto", "ai", "manual")

_SORTS = {
    "collected_at": "collected_at", "updated_at": "updated_at", "size": "size", "path": "path",
    "repo": "repo", "language": "language", "category": "category", "name": "path", "sha256": "sha256",
}


class CatalogError(Exception):
    """Erreur métier : `code` se traduit en statut HTTP côté API."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@dataclass(frozen=True)
class Page:
    items: list[dict]
    total: int
    page: int
    page_size: int
    partial: bool = False       # recherche dans le contenu interrompue par ses limites

    def as_dict(self) -> dict:
        return {"items": self.items, "total": self.total, "page": self.page,
                "page_size": self.page_size, "partial": self.partial}


class Catalog:
    def __init__(self, destination: Path):
        self.store = Store(destination)

    def close(self) -> None:
        self.store.close()

    # -- représentation ----------------------------------------------------------

    @staticmethod
    def _public(row, *, missing: bool | None = None) -> dict:
        path = PurePosixPath(row["path"])
        item = {
            "id": row["id"], "repo": row["repo"], "path": row["path"], "name": path.name,
            "extension": path.suffix.lstrip(".").lower(), "language": row["language"],
            "category": row["category"], "subcategory": row["subcategory"],
            "tags": json.loads(row["tags"] or "[]"), "classified_by": row["classified_by"],
            "license": row["license"], "size": row["size"], "sha256": row["sha256"],
            "local_sha256": row["local_sha256"], "modified": row["local_sha256"] != row["sha256"],
            "branch": row["branch"], "commit": row["commit_sha"], "source_url": row["source_url"],
            "collected_at": row["collected_at"], "updated_at": row["updated_at"],
            "edited_at": row["edited_at"], "status": row["status"],
        }
        if missing is not None:
            item["missing"] = missing
        return item

    def _row(self, file_id: int):
        row = self.store.db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise CatalogError("not_found", "Fichier inconnu.")
        return row

    def _local(self, row) -> Path:
        if row["status"] == "deleted":
            raise CatalogError("deleted", "Ce fichier a été supprimé localement.")
        local = self.store.resolve_local(row)
        if local is None:
            raise CatalogError("missing", "Le fichier local est introuvable sur le disque.")
        return local

    # -- liste, recherche et filtres ------------------------------------------------

    def facets(self) -> dict:
        db = self.store.db
        active = "status = 'active'"
        categories: dict[str, dict[str, int]] = {}
        for r in db.execute(f"SELECT category, subcategory, COUNT(*) n FROM files WHERE {active} "
                            "GROUP BY category, subcategory ORDER BY category, subcategory"):
            categories.setdefault(r["category"], {})[r["subcategory"]] = r["n"]

        def column(name: str) -> list[dict]:
            return [{"value": r[name], "count": r["n"]} for r in db.execute(
                f"SELECT {name}, COUNT(*) n FROM files WHERE {active} AND {name} IS NOT NULL AND {name} != '' "
                f"GROUP BY {name} ORDER BY n DESC, {name} LIMIT 200")]

        totals = db.execute(f"SELECT COUNT(*) n, COALESCE(SUM(size), 0) s FROM files WHERE {active}").fetchone()
        return {
            "categories": [{"category": c, "count": sum(subs.values()),
                            "subcategories": [{"value": s, "count": n} for s, n in subs.items() if s]}
                           for c, subs in categories.items()],
            "languages": column("language"), "licenses": column("license"), "repos": column("repo")[:100],
            "files": totals["n"], "bytes": totals["s"],
        }

    def _filters(self, *, repo, language, category, subcategory, license, extension, status, modified):
        where, args = [], []
        where.append("status = ?")
        args.append("deleted" if status == "deleted" else "active")
        for column, value in (("repo", repo), ("language", language), ("subcategory", subcategory),
                              ("license", license)):
            if value:
                where.append(f"{column} = ?")
                args.append(value)
        if category in ("non-classe", "divers"):           # « Non classé » regroupe l'ancien nom
            where.append("category IN ('non-classe', 'divers')")
        elif category:
            where.append("category = ?")
            args.append(category)
        if extension:
            where.append("lower(path) LIKE ? ESCAPE '\\'")
            args.append("%." + extension.lower().lstrip(".").replace("%", "").replace("_", "\\_"))
        if modified:
            where.append("local_sha256 != sha256")
        return " AND ".join(where), args

    def list_files(self, *, q: str = "", scope: str = "name", repo: str = "", language: str = "",
                   category: str = "", subcategory: str = "", license: str = "", extension: str = "",
                   status: str = "active", modified: bool = False, sort: str = "collected_at",
                   order: str = "desc", page: int = 1, page_size: int = 50) -> Page:
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        column = _SORTS.get(sort, "collected_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        where, args = self._filters(repo=repo, language=language, category=category, subcategory=subcategory,
                                    license=license, extension=extension, status=status, modified=modified)
        q = q.strip()
        order_sql = f"ORDER BY {column} {direction}, id {direction}"
        db = self.store.db

        if not q or scope == "name":
            if q:
                where += " AND (path LIKE ? ESCAPE '\\' OR repo LIKE ? ESCAPE '\\' OR dest_path LIKE ? ESCAPE '\\')"
                args += [_like(q)] * 3
            total = db.execute(f"SELECT COUNT(*) FROM files WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM files WHERE {where} {order_sql} LIMIT ? OFFSET ?",
                              [*args, page_size, (page - 1) * page_size]).fetchall()
            return Page([self._public(r) for r in rows], total, page, page_size)

        # Recherche dans le contenu : lecture bornée (nombre de fichiers, taille, durée).
        needle = q.lower()
        matches: list = []
        partial = False
        started = time.monotonic()
        scanned = 0
        for row in db.execute(f"SELECT * FROM files WHERE {where} {order_sql}", args):
            if scope == "all" and (needle in row["path"].lower() or needle in row["repo"].lower()):
                matches.append(row)
                continue
            if scanned >= SCAN_FILE_LIMIT or time.monotonic() - started > SCAN_SECONDS:
                partial = True
                break
            scanned += 1
            local = self.store.resolve_local(row)
            if local is None or row["size"] > MAX_SCAN_FILE_BYTES:
                continue
            try:
                text = local.read_bytes()[:MAX_SCAN_FILE_BYTES].decode("utf-8", "replace").lower()
            except OSError:
                continue
            if needle in text:
                matches.append(row)
        start = (page - 1) * page_size
        return Page([self._public(r) for r in matches[start:start + page_size]], len(matches),
                    page, page_size, partial)

    # -- lecture ---------------------------------------------------------------------

    def get(self, file_id: int) -> dict:
        row = self._row(file_id)
        missing = row["status"] != "deleted" and self.store.resolve_local(row) is None
        item = self._public(row, missing=missing)
        item["versions"] = self.store.db.execute(
            "SELECT COUNT(*) FROM file_versions WHERE file_id = ?", (file_id,)).fetchone()[0]
        return item

    def read(self, file_id: int, *, offset: int = 0, limit: int | None = None) -> dict:
        """Contenu (ou tranche d'octets pour les gros fichiers). `offset` et `limit` sont en octets."""
        row = self._row(file_id)
        local = self._local(row)
        size = local.stat().st_size
        offset = max(0, offset)
        if limit is None:
            if size > MAX_VIEW_BYTES:
                limit = MAX_CHUNK_BYTES            # trop gros pour tout afficher : première tranche
            else:
                limit = size
        limit = max(1, min(limit, MAX_CHUNK_BYTES if size > MAX_VIEW_BYTES else MAX_VIEW_BYTES))
        with open(local, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read(limit)
        # Une tranche peut couper un caractère UTF-8 en deux : on termine proprement sur un caractère entier.
        text = chunk.decode("utf-8", "ignore") if offset + len(chunk) < size else chunk.decode("utf-8", "replace")
        whole = offset == 0 and len(chunk) >= size
        return {
            "id": file_id, "content": text, "size": size, "offset": offset, "bytes_read": len(chunk),
            "truncated": offset + len(chunk) < size, "complete": whole,
            "sha256": _sha256(chunk) if whole else row["local_sha256"],
            "editable": whole and size <= MAX_EDIT_BYTES,
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        }

    def download(self, file_id: int) -> tuple[Path, str]:
        row = self._row(file_id)
        local = self._local(row)
        return local, local.name

    # -- écriture locale ---------------------------------------------------------------

    def _store_content(self, row, local: Path, data: bytes, reason: str) -> dict:
        """Remplace le contenu local en gardant l'ancien dans l'historique."""
        previous = local.read_bytes()
        digest = _sha256(data)
        if digest == _sha256(previous):
            return self._public(row)
        self.store._atomic_write(local, data)
        try:
            with self.store.db:
                self.store.add_version(row["id"], previous, reason, commit=False)
                self.store.db.execute(
                    "UPDATE files SET local_sha256 = ?, size = ?, edited_at = ? WHERE id = ?",
                    (digest, len(data), None if digest == row["sha256"] else time.time(), row["id"]))
        except BaseException:
            self.store._atomic_write(local, previous)
            raise
        return self.get(row["id"])

    def write(self, file_id: int, content: str, expected_sha256: str) -> dict:
        """Enregistre une modification locale. `expected_sha256` protège d'un écrasement concurrent."""
        data = content.encode("utf-8")
        if len(data) > MAX_EDIT_BYTES:
            raise CatalogError("too_large", f"Contenu trop volumineux (maximum {MAX_EDIT_BYTES // 1024} Ko).")
        if b"\x00" in data:
            raise CatalogError("binary", "Contenu binaire refusé.")
        row = self._row(file_id)
        local = self._local(row)
        if local.stat().st_size > MAX_EDIT_BYTES:
            raise CatalogError("too_large", "Fichier trop gros pour être modifié ici (lecture seule).")
        if _sha256(local.read_bytes()) != expected_sha256:
            raise CatalogError("conflict", "Le fichier a changé depuis son ouverture : recharge-le avant d'enregistrer.")
        return self._store_content(row, local, data, "edition-locale")

    def versions(self, file_id: int) -> list[dict]:
        self._row(file_id)
        return [{"id": r["id"], "sha256": r["sha256"], "size": r["size"], "reason": r["reason"],
                 "created_at": r["created_at"]} for r in self.store.db.execute(
            "SELECT id, sha256, size, reason, created_at FROM file_versions WHERE file_id = ? ORDER BY id DESC",
            (file_id,))]

    def version_content(self, file_id: int, version_id: int) -> dict:
        found = self.store.db.execute(
            "SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (version_id, file_id)).fetchone()
        if found is None:
            raise CatalogError("not_found", "Version inconnue.")
        return {"id": found["id"], "sha256": found["sha256"], "size": found["size"], "reason": found["reason"],
                "created_at": found["created_at"], "content": bytes(found["content"]).decode("utf-8", "replace")}

    def restore(self, file_id: int, version_id: int) -> dict:
        found = self.store.db.execute(
            "SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (version_id, file_id)).fetchone()
        if found is None:
            raise CatalogError("not_found", "Version inconnue.")
        row = self._row(file_id)
        data = bytes(found["content"])
        if row["status"] == "deleted":                      # fichier supprimé : on le recrée
            target, relative = self.store._free_path(row, found["sha256"])
            self.store._atomic_write(target, data)
            with self.store.db:
                self.store.db.execute(
                    "UPDATE files SET status = 'active', local_sha256 = ?, size = ?, dest_path = ?, edited_at = ? "
                    "WHERE id = ?", (found["sha256"], len(data), relative,
                                     None if found["sha256"] == row["sha256"] else time.time(), file_id))
            return self.get(file_id)
        return self._store_content(row, self._local(row), data, "restauration")

    def delete(self, file_id: int) -> None:
        """Supprime le fichier local ; la pierre tombale évite qu'une collecte le retélécharge."""
        row = self._row(file_id)
        if row["status"] == "deleted":
            return
        local = self.store.resolve_local(row)
        previous = local.read_bytes() if local is not None else None
        with self.store.db:
            if previous is not None:
                self.store.add_version(file_id, previous, "suppression", commit=False)
            self.store.db.execute("UPDATE files SET status = 'deleted', updated_at = ? WHERE id = ?",
                                  (time.time(), file_id))
        if local is not None:
            local.unlink(missing_ok=True)

    # -- classement ----------------------------------------------------------------------

    @staticmethod
    def _folder_part(value: str, what: str) -> str:
        if value.strip().lower() in ("non classé", "non classe"):
            return DEFAULT_CATEGORY
        part = slugify_part(value)
        if part is None or not is_valid_folder_name(part):
            raise CatalogError("invalid", f"{what} invalide (lettres, chiffres, - _ . + ; 40 caractères au plus).")
        return part

    def set_category(self, file_id: int, category: str, subcategory: str = "", *,
                     tags: list[str] | None = None, source: str = "manual") -> dict:
        """Reclasse un fichier : il est déplacé dans `<catégorie>[/<sous-catégorie>]/<langage>/`."""
        if source not in CLASSIFIED_BY:
            raise CatalogError("invalid", "Origine du classement inconnue.")
        category = self._folder_part(category, "Catégorie")
        subcategory = self._folder_part(subcategory, "Sous-catégorie") if subcategory.strip() else ""
        if category == DEFAULT_CATEGORY:
            subcategory = ""
        row = self._row(file_id)
        local = self._local(row)
        try:
            folder = self.store._category_folder(category, subcategory) / row["language"]
        except ValueError as exc:
            raise CatalogError("invalid", "Catégorie hors de la bibliothèque.") from exc
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / MARKER
        if not marker.exists():
            marker.write_text(row["language"], encoding="utf-8")

        target = local
        if local.parent.resolve() != folder.resolve():
            name, counter = local.name, 2
            while (folder / name).exists():
                name = f"{local.stem}-{counter}{local.suffix}"
                counter += 1
            target = folder / name
            if not target.resolve().is_relative_to(self.store.destination.resolve()):
                raise CatalogError("invalid", "Destination hors de la bibliothèque.")
            os.replace(local, target)
        relative = target.relative_to(self.store.destination).as_posix()
        final_tags = list(dict.fromkeys(
            [category, subcategory, *(tags if tags is not None else json.loads(row["tags"] or "[]"))]))
        final_tags = [t for t in final_tags if t and is_valid_folder_name(t)][:10]
        try:
            with self.store.db:
                self.store.db.execute(
                    "UPDATE files SET category = ?, subcategory = ?, tags = ?, classified_by = ?, dest_path = ? "
                    "WHERE id = ?", (category, subcategory, json.dumps(final_tags), source, relative, file_id))
        except BaseException:
            if target != local:
                os.replace(target, local)
            raise
        self.store._names.add(target.name.lower())
        return self.get(file_id)

    def set_tags(self, file_id: int, tags: list[str], *, source: str = "manual") -> dict:
        cleaned = []
        for tag in tags:
            part = slugify_part(tag)
            if part and part not in cleaned:
                cleaned.append(part)
        row = self._row(file_id)
        with self.store.db:
            self.store.db.execute("UPDATE files SET tags = ?, classified_by = ? WHERE id = ?",
                                  (json.dumps(cleaned[:10]), source, file_id))
        return self.get(row["id"])

    def mark_ai_checked(self, file_id: int) -> None:
        """L'analyse IA n'a rien trouvé : le fichier reste « non classé » mais ne sera pas reproposé."""
        with self.store.db:
            self.store.db.execute("UPDATE files SET classified_by = 'ai' WHERE id = ? AND classified_by = 'auto'",
                                  (file_id,))

    def unclassified(self, limit: int, *, only_auto: bool = True) -> list[dict]:
        """Fichiers sans catégorie (candidats à l'analyse IA), jamais analysés ni classés à la main."""
        clause = "classified_by = 'auto'" if only_auto else "classified_by != 'manual'"
        rows = self.store.db.execute(
            f"SELECT * FROM files WHERE status = 'active' AND category IN ('non-classe', 'divers') AND {clause} "
            "ORDER BY id LIMIT ?", (max(1, limit),)).fetchall()
        return [self._public(r) for r in rows]
