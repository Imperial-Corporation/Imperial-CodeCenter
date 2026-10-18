# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Comptes utilisateurs, sessions par cookie et discussions enregistrées.

* Pseudo + mot de passe. Le mot de passe n'est jamais stocké : seulement une
  empreinte `scrypt` salée (comparaison en temps constant).
* Session : un jeton aléatoire de 256 bits est envoyé dans un cookie
  `HttpOnly`/`SameSite=Lax` ; la base ne conserve que son empreinte SHA-256
  (une fuite de la base ne permet donc pas de se connecter).
* Clés et configuration IA **par compte**, chiffrées avec le mot de passe :
  chaque compte a une clé de données aléatoire (DEK) ; elle est « enveloppée »
  (AES-256-GCM) par une clé dérivée du mot de passe (scrypt) et, pour la durée
  d'une session, par une clé dérivée du jeton de session (HKDF). La base seule
  ne permet donc jamais de lire la configuration d'un compte : il faut son mot
  de passe, ou le cookie de session ouvert. Mot de passe oublié = clés perdues
  (à ressaisir) ; elles ne sont lisibles par aucun autre compte, ni par
  l'administrateur.
* Discussions « définitives » : une discussion de l'IA est temporaire (en
  mémoire) tant que l'utilisateur ne l'a pas enregistrée ; une fois
  enregistrée, elle est conservée dans la base du compte.

Stockage : SQLite (bibliothèque standard), fichier en 0600.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128
SESSION_TTL_S = 30 * 24 * 3600
MAX_CHATS_PER_USER = 200
MAX_CHAT_BYTES = 3_000_000
MAX_TITLE_CHARS = 120

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _KEY_LEN = 2**14, 8, 1, 32

# Anti force brute : échecs consécutifs tolérés par (pseudo, IP) puis par IP.
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60
MAX_FAILURES_PER_IP = 30
# Créations de compte : 10 par heure et par IP.
MAX_REGISTRATIONS_PER_IP = 10
REGISTER_WINDOW_S = 3600


class AccountError(Exception):
    """Erreur métier (code stable + message affichable)."""

    def __init__(self, code: str, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# Mots de passe
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_LEN)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(expected), maxmem=128 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Empreinte factice : un pseudo inconnu coûte autant de temps qu'un mot de passe faux.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def normalize_username(raw: str) -> str:
    return unicodedata.normalize("NFKC", str(raw or "")).strip()


def validate_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise AccountError(
            "invalid_username", "Pseudo invalide : 3 à 32 caractères (lettres, chiffres, « _ », « - », « . »).")
    if len(password) < PASSWORD_MIN:
        raise AccountError("weak_password", f"Mot de passe trop court (minimum {PASSWORD_MIN} caractères).")
    if len(password) > PASSWORD_MAX:
        raise AccountError("invalid_password", f"Mot de passe trop long (maximum {PASSWORD_MAX} caractères).")
    if password.lower() == username.lower():
        raise AccountError("weak_password", "Le mot de passe ne peut pas être identique au pseudo.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Clé de données (DEK) : enveloppée par le mot de passe et par la session
# --------------------------------------------------------------------------

def _seal(key: bytes, plaintext: bytes, aad: bytes) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext, aad)).decode()


def _unseal(key: bytes, blob: str, aad: bytes) -> bytes:
    raw = base64.b64decode(blob)
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad)
    except InvalidTag:
        raise AccountError("locked", "Données chiffrées illisibles : reconnecte-toi.", 401) from None


def _kek(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                          dklen=32, maxmem=128 * 1024 * 1024)


def _session_key(token: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"icc-session-dek-v1").derive(
        token.encode("utf-8"))


def _wrap_for_password(user_id: str, password: str, dek: bytes) -> tuple[str, str]:
    salt = os.urandom(16)
    return base64.b64encode(salt).decode(), _seal(_kek(password, salt), dek, f"dek:{user_id}".encode())


def _unwrap_with_password(user_id: str, password: str, salt_b64: str, wrapped: str) -> bytes:
    return _unseal(_kek(password, base64.b64decode(salt_b64)), wrapped, f"dek:{user_id}".encode())


# --------------------------------------------------------------------------
# Stockage
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    kek_salt TEXT,
    wrapped_dek TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    wrapped_dek TEXT
);
CREATE TABLE IF NOT EXISTS ai_configs (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    blob TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    turns TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_user ON chats(user_id, updated_at DESC);
"""


class AccountStore:
    def __init__(self, path: Path, *, registration_open: bool = True, clock=time.time):
        self.path = Path(path)
        self.registration_open = registration_open
        self._clock = clock
        self._lock = threading.RLock()
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._ip_failures: dict[str, list[float]] = {}
        self._registrations: dict[str, list[float]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._migrate()
        if not existed:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _migrate(self) -> None:
        """Bases de la 1.0.1 : ajoute les colonnes de chiffrement."""
        for table, column in (("users", "kek_salt"), ("users", "wrapped_dek"), ("sessions", "wrapped_dek")):
            columns = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    @classmethod
    def from_environment(cls, data_dir: Path) -> "AccountStore":
        mode = os.environ.get("CODECENTER_REGISTRATION", "open").strip().lower()
        return cls(Path(data_dir) / "accounts.db", registration_open=mode not in {"closed", "off", "0", "false", "non"})

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def is_admin(self, user_id: str) -> bool:
        """Administrateur de l'instance : le premier compte créé."""
        with self._lock:
            row = self._db.execute("SELECT id FROM users ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
        return bool(row and row["id"] == user_id)

    def user_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # -- comptes ---------------------------------------------------------------

    def _check_register_throttle(self, ip: str) -> None:
        now = self._clock()
        recent = [t for t in self._registrations.get(ip, []) if now - t < REGISTER_WINDOW_S]
        if len(recent) >= MAX_REGISTRATIONS_PER_IP:
            self._registrations[ip] = recent
            raise AccountError("too_many_registrations", "Trop de créations de compte depuis cette adresse : réessaie plus tard.",
                               429, retry_after=max(1, int(REGISTER_WINDOW_S - (now - recent[0]))))
        recent.append(now)
        self._registrations[ip] = recent

    def register(self, username: str, password: str, ip: str = "") -> dict:
        if not self.registration_open:
            raise AccountError("registration_closed", "Les inscriptions sont fermées sur cette instance.", 403)
        with self._lock:
            self._check_register_throttle(ip)
        username = normalize_username(username)
        validate_credentials(username, password)
        password_hash = hash_password(password)  # hors verrou : coûteux
        user_id = uuid.uuid4().hex
        dek = os.urandom(32)
        kek_salt, wrapped = _wrap_for_password(user_id, password, dek)
        try:
            with self._tx() as db:
                db.execute(
                    "INSERT INTO users(id, username, password_hash, created_at, kek_salt, wrapped_dek) "
                    "VALUES (?,?,?,?,?,?)",
                    (user_id, username, password_hash, self._clock(), kek_salt, wrapped))
        except sqlite3.IntegrityError:
            raise AccountError("username_taken", "Ce pseudo est déjà pris.", 409) from None
        return {"id": user_id, "username": username, "dek": dek}

    def _check_throttle(self, username: str, ip: str) -> None:
        now = self._clock()
        key = (username.lower(), ip)
        recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW_S]
        recent_ip = [t for t in self._ip_failures.get(ip, []) if now - t < FAILURE_WINDOW_S]
        self._failures[key], self._ip_failures[ip] = recent, recent_ip
        if len(recent) >= MAX_FAILURES or len(recent_ip) >= MAX_FAILURES_PER_IP:
            oldest = recent[0] if len(recent) >= MAX_FAILURES else recent_ip[0]
            wait = max(1, int(FAILURE_WINDOW_S - (now - oldest)))
            raise AccountError("too_many_attempts", "Trop de tentatives : réessaie plus tard.", 429, retry_after=wait)

    def authenticate(self, username: str, password: str, ip: str = "") -> dict:
        username = normalize_username(username)
        with self._lock:
            self._check_throttle(username, ip)
            row = self._db.execute(
                "SELECT id, username, password_hash, kek_salt, wrapped_dek FROM users WHERE username = ?",
                (username,)).fetchone()
        ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
        if not row or not ok:
            with self._lock:
                self._failures.setdefault((username.lower(), ip), []).append(self._clock())
                self._ip_failures.setdefault(ip, []).append(self._clock())
            raise AccountError("bad_credentials", "Pseudo ou mot de passe incorrect.", 401)
        with self._lock:
            self._failures.pop((username.lower(), ip), None)
        if row["wrapped_dek"] and row["kek_salt"]:
            dek = _unwrap_with_password(row["id"], password, row["kek_salt"], row["wrapped_dek"])
        else:  # compte créé avant le chiffrement par compte : on génère la clé maintenant
            dek = os.urandom(32)
            kek_salt, wrapped = _wrap_for_password(row["id"], password, dek)
            with self._tx() as db:
                db.execute("UPDATE users SET kek_salt = ?, wrapped_dek = ? WHERE id = ?",
                           (kek_salt, wrapped, row["id"]))
        return {"id": row["id"], "username": row["username"], "dek": dek}

    # -- sessions ----------------------------------------------------------------

    def create_session(self, user_id: str, dek: bytes | None = None) -> str:
        """Ouvre une session ; `dek` (clé de données du compte) n'y est conservée qu'enveloppée par le jeton."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        wrapped = _seal(_session_key(token), dek, f"sess:{user_id}".encode()) if dek else None
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, wrapped_dek) VALUES (?,?,?,?,?)",
                (_token_hash(token), user_id, now, now + SESSION_TTL_S, wrapped))
        return token

    def dek_for_token(self, token: str | None, user_id: str) -> bytes | None:
        """Clé de données du compte, déverrouillée par le jeton de la session en cours."""
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT wrapped_dek FROM sessions WHERE token_hash = ? AND user_id = ? AND expires_at > ?",
                (_token_hash(token), user_id, self._clock())).fetchone()
        if not row or not row["wrapped_dek"]:
            return None
        return _unseal(_session_key(token), row["wrapped_dek"], f"sess:{user_id}".encode())

    # -- configuration IA chiffrée par compte ------------------------------------

    def load_ai_config(self, user_id: str, dek: bytes) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT blob FROM ai_configs WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return json.loads(_unseal(dek, row["blob"], f"cfg:{user_id}".encode()).decode("utf-8"))

    def save_ai_config(self, user_id: str, dek: bytes, data: dict) -> None:
        blob = _seal(dek, json.dumps(data, ensure_ascii=False).encode("utf-8"), f"cfg:{user_id}".encode())
        with self._tx() as db:
            db.execute(
                "INSERT INTO ai_configs(user_id, blob, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET blob = excluded.blob, updated_at = excluded.updated_at",
                (user_id, blob, self._clock()))

    def user_for_token(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?", (_token_hash(token), self._clock())).fetchone()
        return {"id": row["id"], "username": row["username"]} if row else None

    def end_session(self, token: str | None) -> None:
        if token:
            with self._tx() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def change_password(self, user_id: str, current: str, new: str, keep_token: str | None = None) -> None:
        with self._lock:
            row = self._db.execute(
                "SELECT username, password_hash, kek_salt, wrapped_dek FROM users WHERE id = ?",
                (user_id,)).fetchone()
        if not row or not verify_password(current, row["password_hash"]):
            raise AccountError("bad_credentials", "Mot de passe actuel incorrect.", 401)
        validate_credentials(row["username"], new)
        new_hash = hash_password(new)
        dek = (_unwrap_with_password(user_id, current, row["kek_salt"], row["wrapped_dek"])
               if row["wrapped_dek"] and row["kek_salt"] else os.urandom(32))
        kek_salt, wrapped = _wrap_for_password(user_id, new, dek)  # la configuration reste lisible
        with self._tx() as db:
            db.execute("UPDATE users SET password_hash = ?, kek_salt = ?, wrapped_dek = ? WHERE id = ?",
                       (new_hash, kek_salt, wrapped, user_id))
            # Les autres appareils doivent se reconnecter.
            if keep_token:
                db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                           (user_id, _token_hash(keep_token)))
            else:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def delete_account(self, user_id: str, password: str) -> None:
        with self._lock:
            row = self._db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise AccountError("bad_credentials", "Mot de passe incorrect.", 401)
        with self._tx() as db:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))  # sessions et chats en cascade

    # -- discussions enregistrées ------------------------------------------------

    @staticmethod
    def _title(text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        return (cleaned[:MAX_TITLE_CHARS - 1] + "…") if len(cleaned) > MAX_TITLE_CHARS else (cleaned or "Discussion")

    def save_chat(self, user_id: str, title: str, turns: list[dict]) -> dict:
        blob = json.dumps(turns, ensure_ascii=False, separators=(",", ":"))
        if len(blob.encode("utf-8")) > MAX_CHAT_BYTES:
            raise AccountError("chat_too_large", "Discussion trop volumineuse pour être enregistrée.", 413)
        chat_id, now = uuid.uuid4().hex, self._clock()
        with self._tx() as db:
            count = db.execute("SELECT COUNT(*) FROM chats WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_CHATS_PER_USER:
                raise AccountError("too_many_chats", f"Limite de {MAX_CHATS_PER_USER} discussions atteinte : supprime-en.", 409)
            db.execute(
                "INSERT INTO chats(id, user_id, title, created_at, updated_at, turn_count, turns) VALUES (?,?,?,?,?,?,?)",
                (chat_id, user_id, self._title(title), now, now, len(turns), blob))
        return {"id": chat_id, "title": self._title(title), "updated_at": now, "turn_count": len(turns)}

    def list_chats(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, created_at, updated_at, turn_count FROM chats WHERE user_id = ? "
                "ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [{"id": r["id"], "title": r["title"], "created_at": r["created_at"],
                 "updated_at": r["updated_at"], "turn_count": r["turn_count"]} for r in rows]

    def get_chat(self, user_id: str, chat_id: str) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT id, title, created_at, updated_at, turns FROM chats WHERE id = ? AND user_id = ?",
                (chat_id, user_id)).fetchone()
        if not row:
            raise AccountError("chat_not_found", "Discussion introuvable.", 404)
        return {"id": row["id"], "title": row["title"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "turns": json.loads(row["turns"])}

    def append_turn(self, user_id: str, chat_id: str, turn: dict) -> None:
        with self._tx() as db:
            row = db.execute("SELECT turns FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
            if not row:
                raise AccountError("chat_not_found", "Discussion introuvable.", 404)
            turns = json.loads(row["turns"])
            turns.append(turn)
            blob = json.dumps(turns, ensure_ascii=False, separators=(",", ":"))
            if len(blob.encode("utf-8")) > MAX_CHAT_BYTES:
                raise AccountError("chat_too_large", "Discussion trop volumineuse pour être poursuivie.", 413)
            db.execute("UPDATE chats SET turns = ?, turn_count = ?, updated_at = ? WHERE id = ?",
                       (blob, len(turns), self._clock(), chat_id))

    def rename_chat(self, user_id: str, chat_id: str, title: str) -> dict:
        title = self._title(title)
        with self._tx() as db:
            cursor = db.execute("UPDATE chats SET title = ? WHERE id = ? AND user_id = ?", (title, chat_id, user_id))
            if cursor.rowcount == 0:
                raise AccountError("chat_not_found", "Discussion introuvable.", 404)
        return {"id": chat_id, "title": title}

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        with self._tx() as db:
            cursor = db.execute("DELETE FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id))
            if cursor.rowcount == 0:
                raise AccountError("chat_not_found", "Discussion introuvable.", 404)
