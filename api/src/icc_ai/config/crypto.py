"""Chiffrement des secrets au repos (Fernet : AES-128-CBC + HMAC-SHA256).

La clé maître n'est jamais stockée avec les données chiffrées. Ordre de
résolution :
1. `CODECENTER_SECRET_KEY` : clé dans l'environnement (production : fichier
   `/etc/.../.env`, propriété root, mode 0600, lu par systemd) ;
2. `CODECENTER_SECRET_KEY_FILE` : chemin d'un fichier de clé ;
3. repli développement : fichier généré dans `CODECENTER_KEY_DIR`
   (par défaut `.local/` du dépôt, ignoré par git), mode 0600.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..errors import ConfigError

logger = logging.getLogger(__name__)

PREFIX = "fernet:v1:"


def _write_private(path: Path, data: bytes) -> None:
    """Écrit un fichier en 0600 de façon atomique."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_private_file(path: Path, data: bytes) -> None:
    _write_private(path, data)


def _load_master_key(default_key_dir: Path) -> bytes:
    env_key = os.environ.get("CODECENTER_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode()

    key_file = os.environ.get("CODECENTER_SECRET_KEY_FILE", "").strip()
    path = Path(key_file) if key_file else Path(
        os.environ.get("CODECENTER_KEY_DIR", str(default_key_dir))
    ) / "secret.key"

    if path.is_file():
        mode = path.stat().st_mode & 0o077
        if mode:
            logger.warning(
                "Permissions trop larges sur le fichier de clé (%s) : "
                "restreins-les avec chmod 600.", path.name,
            )
        return path.read_bytes().strip()

    if key_file:
        raise ConfigError("CODECENTER_SECRET_KEY_FILE pointe vers un fichier absent.")

    logger.warning(
        "Aucune clé CODECENTER_SECRET_KEY : génération d'une clé locale "
        "(%s). En production, définis CODECENTER_SECRET_KEY.", path.parent,
    )
    key = Fernet.generate_key()
    _write_private(path, key)
    return key


class SecretBox:
    """Chiffre et déchiffre les valeurs sensibles."""

    def __init__(self, key: bytes):
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                "Clé de chiffrement invalide (attendu : clé Fernet base64 de 32 octets)."
            ) from exc

    @classmethod
    def from_environment(cls, default_key_dir: Path) -> "SecretBox":
        return cls(_load_master_key(default_key_dir))

    def encrypt(self, plaintext: str) -> str:
        return PREFIX + self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token.startswith(PREFIX):
            raise ConfigError("Format de secret inconnu.")
        try:
            return self._fernet.decrypt(token[len(PREFIX):].encode()).decode()
        except InvalidToken as exc:
            raise ConfigError(
                "Impossible de déchiffrer la clé API : la clé de chiffrement a changé. "
                "Saisis de nouveau la clé API dans le panneau."
            ) from exc
