# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Sauvegarde et restauration des données de l'instance.

Une sauvegarde est une archive `tar.gz` contenant :

* `accounts.db` : copie cohérente de la base des comptes (API de sauvegarde SQLite) ;
* `data/…`      : le reste du dossier de données (état du crawler, jeton GitHub chiffré…) ;
* `secret.key`  : la clé qui chiffre le jeton GitHub, si elle est dans le dossier `.local` ;
* `codes/…`     : la bibliothèque de code (option `with_codes`) ;
* `manifest.json` : version, date, liste des fichiers avec leur SHA-256.

Avec une phrase secrète, l'archive est chiffrée (AES-256-GCM par blocs, clé dérivée par scrypt) :
extension `.icc-backup`. Sans phrase secrète : `.tar.gz` en clair — elle contient les empreintes de
mots de passe et la configuration IA chiffrée des comptes, à protéger comme un secret.

La restauration se fait **à l'arrêt** (CLI) : on ne remplace pas une base ouverte. Elle vérifie le
manifeste, refuse tout chemin suspect (traversée, lien symbolique) et ne touche à rien si
l'archive est invalide.

    python -m icc_ai.backup create [--out DOSSIER] [--with-codes] [--passphrase-file F]
    python -m icc_ai.backup restore ARCHIVE [--passphrase-file F] [--force]
    python -m icc_ai.backup list
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .about import VERSION

MAGIC = b"ICCB1\n"
CHUNK = 1024 * 1024
SCRYPT = dict(n=2**15, r=8, p=1)
MANIFEST = "manifest.json"
BACKUP_DIR_NAME = "backups"
SKIP_DIRS = {BACKUP_DIR_NAME, "__pycache__", "uploads-tmp"}
SKIP_FILES = {"accounts.db", "accounts.db-wal", "accounts.db-shm"}


class BackupError(Exception):
    pass


# --------------------------------------------------------------------------
# Chiffrement par blocs
# --------------------------------------------------------------------------

def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, dklen=32, maxmem=256 * 1024 * 1024, **SCRYPT)


def _nonce(counter: int, last: bool) -> bytes:
    return struct.pack(">QB", counter, 1 if last else 0).rjust(12, b"\0")


def encrypt_file(source: Path, target: Path, passphrase: str) -> None:
    salt = os.urandom(16)
    aes = AESGCM(_key(passphrase, salt))
    with source.open("rb") as src, target.open("wb") as out:
        out.write(MAGIC + salt)
        counter = 0
        block = src.read(CHUNK)
        while True:
            following = src.read(CHUNK)
            last = not following
            out.write(aes.encrypt(_nonce(counter, last), block, MAGIC))
            if last:
                break
            block, counter = following, counter + 1


def decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    with source.open("rb") as src, target.open("wb") as out:
        head = src.read(len(MAGIC) + 16)
        if len(head) < len(MAGIC) + 16 or head[: len(MAGIC)] != MAGIC:
            raise BackupError("Ce fichier n'est pas une sauvegarde chiffrée CodeCenter.")
        aes = AESGCM(_key(passphrase, head[len(MAGIC):]))
        counter = 0
        block = src.read(CHUNK + 16)
        try:
            while True:
                following = src.read(CHUNK + 16)
                last = not following
                out.write(aes.decrypt(_nonce(counter, last), block, MAGIC))
                if last:
                    break
                block, counter = following, counter + 1
        except InvalidTag:
            raise BackupError("Phrase secrète incorrecte ou sauvegarde corrompue.") from None


def is_encrypted(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


# --------------------------------------------------------------------------
# Création
# --------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk(root: Path):
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(current) / d).is_symlink()]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def create_backup(
    data_dir: Path,
    out_dir: Path,
    *,
    accounts=None,
    codes_dir: Path | None = None,
    secret_key: Path | None = None,
    passphrase: str | None = None,
    clock=time.time,
) -> Path:
    """Crée une sauvegarde dans `out_dir` ; renvoie son chemin."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)
    except OSError:
        pass
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(clock()))
    final = out_dir / (f"icc-backup-{stamp}" + (".icc-backup" if passphrase else ".tar.gz"))
    entries: list[tuple[Path, str]] = []

    with tempfile.TemporaryDirectory(prefix="icc-backup-") as tmp:
        tmp_dir = Path(tmp)
        if accounts is not None:
            snapshot = tmp_dir / "accounts.db"
            accounts.snapshot_to(snapshot)
            entries.append((snapshot, "accounts.db"))
        elif (data_dir / "accounts.db").is_file():
            import sqlite3

            snapshot = tmp_dir / "accounts.db"
            source = sqlite3.connect(data_dir / "accounts.db")
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                source.close(), target.close()
            entries.append((snapshot, "accounts.db"))

        if data_dir.is_dir():
            for path in _walk(data_dir):
                if path.name in SKIP_FILES and path.parent == data_dir:
                    continue
                entries.append((path, "data/" + path.relative_to(data_dir).as_posix()))
        if secret_key and Path(secret_key).is_file():
            entries.append((Path(secret_key), "secret.key"))
        if codes_dir and Path(codes_dir).is_dir():
            for path in _walk(Path(codes_dir)):
                entries.append((path, "codes/" + path.relative_to(codes_dir).as_posix()))

        manifest = {
            "format": 1, "version": VERSION, "created": clock(),
            "files": {name: _sha256(path) for path, name in entries},
            "with_codes": bool(codes_dir),
        }
        archive = tmp_dir / "backup.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            raw = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mtime, info.mode = len(raw), int(clock()), 0o600
            tar.addfile(info, io.BytesIO(raw))
            for path, name in entries:
                info = tar.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600 if info.isfile() else 0o700
                with path.open("rb") as handle:
                    tar.addfile(info, handle)

        if passphrase:
            encrypt_file(archive, final, passphrase)
        else:
            shutil.copyfile(archive, final)
    os.chmod(final, 0o600)
    return final


def prune(out_dir: Path, keep: int) -> list[Path]:
    """Ne garde que les `keep` sauvegardes les plus récentes."""
    files = sorted(list_backups(out_dir), key=lambda b: b["name"], reverse=True)
    removed: list[Path] = []
    for item in files[max(keep, 1):]:
        path = Path(out_dir) / item["name"]
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def list_backups(out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return []
    items = []
    for path in out_dir.iterdir():
        if path.is_file() and path.name.startswith("icc-backup-") and path.name.endswith((".tar.gz", ".icc-backup")):
            stat = path.stat()
            items.append({"name": path.name, "size": stat.st_size, "created": stat.st_mtime,
                          "encrypted": path.name.endswith(".icc-backup")})
    return sorted(items, key=lambda i: i["name"], reverse=True)


# --------------------------------------------------------------------------
# Restauration
# --------------------------------------------------------------------------

def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for member in tar.getmembers():
        name = member.name
        if not (member.isfile() or member.isdir()):
            raise BackupError(f"Entrée non autorisée dans l'archive : {name}")
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts or "\\" in name:
            raise BackupError(f"Chemin suspect dans l'archive : {name}")
        members.append(member)
    return members


def verify_archive(archive: Path) -> dict:
    """Vérifie le manifeste et les empreintes ; renvoie le manifeste."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = {m.name: m for m in _safe_members(tar)}
            if MANIFEST not in members:
                raise BackupError("Manifeste absent : ce n'est pas une sauvegarde CodeCenter.")
            manifest = json.load(tar.extractfile(members[MANIFEST]))
            for name, expected in manifest.get("files", {}).items():
                member = members.get(name)
                if member is None:
                    raise BackupError(f"Fichier manquant dans l'archive : {name}")
                digest = hashlib.sha256()
                handle = tar.extractfile(member)
                for block in iter(lambda: handle.read(CHUNK), b""):
                    digest.update(block)
                if digest.hexdigest() != expected:
                    raise BackupError(f"Empreinte invalide : {name}")
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise BackupError(f"Archive illisible : {exc}") from exc
    return manifest


def restore_backup(
    archive: Path,
    data_dir: Path,
    *,
    codes_dir: Path | None = None,
    secret_key: Path | None = None,
    passphrase: str | None = None,
    force: bool = False,
) -> dict:
    """Restaure une sauvegarde (instance arrêtée). Les fichiers remplacés sont gardés en `*.avant-restauration`."""
    archive, data_dir = Path(archive), Path(data_dir)
    with tempfile.TemporaryDirectory(prefix="icc-restore-") as tmp:
        plain = archive
        if is_encrypted(archive):
            if not passphrase:
                raise BackupError("Sauvegarde chiffrée : fournis la phrase secrète.")
            plain = Path(tmp) / "plain.tar.gz"
            decrypt_file(archive, plain, passphrase)
        manifest = verify_archive(plain)
        if manifest.get("format") != 1:
            raise BackupError("Format de sauvegarde inconnu (créé par une version plus récente ?).")

        if (data_dir / "accounts.db").exists() and not force:
            raise BackupError("Des données existent déjà ici : relance avec --force pour les remplacer "
                              "(elles seront conservées en *.avant-restauration).")

        stage = Path(tmp) / "stage"
        with tarfile.open(plain, "r:gz") as tar:
            members = _safe_members(tar)
            tar.extractall(stage, members=members, filter="data")

        restored = {"accounts": False, "data_files": 0, "codes_files": 0, "secret": False}
        data_dir.mkdir(parents=True, exist_ok=True)

        def put(src: Path, dst: Path) -> None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.move(str(dst), str(dst) + ".avant-restauration")
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)

        accounts = stage / "accounts.db"
        if accounts.is_file():
            for suffix in ("-wal", "-shm"):
                leftover = data_dir / f"accounts.db{suffix}"
                if leftover.exists():
                    leftover.unlink()
            put(accounts, data_dir / "accounts.db")
            restored["accounts"] = True
        for path in (stage / "data").rglob("*") if (stage / "data").is_dir() else []:
            if path.is_file():
                put(path, data_dir / path.relative_to(stage / "data"))
                restored["data_files"] += 1
        if secret_key and (stage / "secret.key").is_file():
            put(stage / "secret.key", Path(secret_key))
            restored["secret"] = True
        if codes_dir and (stage / "codes").is_dir():
            for path in (stage / "codes").rglob("*"):
                if path.is_file():
                    target = Path(codes_dir) / path.relative_to(stage / "codes")
                    if not target.exists():  # on n'écrase jamais un code existant
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
                        restored["codes_files"] += 1
        restored["version"] = manifest.get("version")
        return restored


# --------------------------------------------------------------------------
# Environnement et ligne de commande
# --------------------------------------------------------------------------

def _paths():
    """(dossier de données, fichier de clé, bibliothèque) — mêmes règles que le reste de l'application."""
    from .library import Library
    from .services import ROOT, data_dir

    data = data_dir()
    key_file = os.environ.get("CODECENTER_SECRET_KEY_FILE", "").strip()
    secret = Path(key_file) if key_file else Path(os.environ.get("CODECENTER_KEY_DIR", str(ROOT / ".local"))) / "secret.key"
    configured = os.environ.get("CODECENTER_WATCH_PATHS", "")
    first = next((p for p in configured.split(os.pathsep) if p.strip()), "")
    return data, secret, Path(first) if first else data / "codes"


def _passphrase(args) -> str | None:
    if getattr(args, "passphrase_file", None):
        return Path(args.passphrase_file).read_text().strip() or None
    return os.environ.get("ICC_BACKUP_PASSPHRASE") or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icc-backup", description="Sauvegarde et restauration d'Imperial CodeCenter.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create", help="crée une sauvegarde")
    create.add_argument("--out")
    create.add_argument("--with-codes", action="store_true", help="inclut la bibliothèque de code")
    create.add_argument("--passphrase-file")
    restore = sub.add_parser("restore", help="restaure une sauvegarde (instance arrêtée)")
    restore.add_argument("archive")
    restore.add_argument("--passphrase-file")
    restore.add_argument("--force", action="store_true")
    restore.add_argument("--with-codes", action="store_true")
    sub.add_parser("list", help="liste les sauvegardes")
    args = parser.parse_args(argv)

    data, secret, codes = _paths()
    try:
        if args.cmd == "create":
            out = Path(args.out) if args.out else data / BACKUP_DIR_NAME
            path = create_backup(data, out, codes_dir=codes if args.with_codes else None,
                                 secret_key=secret, passphrase=_passphrase(args))
            print(f"Sauvegarde créée : {path}")
        elif args.cmd == "restore":
            report = restore_backup(Path(args.archive), data, codes_dir=codes if args.with_codes else None,
                                    secret_key=secret, passphrase=_passphrase(args), force=args.force)
            print("Restauration terminée :", json.dumps(report, ensure_ascii=False))
        else:
            for item in list_backups(data / BACKUP_DIR_NAME):
                print(f"{item['name']}  {item['size'] // 1024} Kio{'  (chiffrée)' if item['encrypted'] else ''}")
    except BackupError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
