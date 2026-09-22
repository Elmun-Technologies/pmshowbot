"""Security primitives used by the multi-tenant control plane.

Telegram bot tokens are encrypted at rest with Fernet and tenant-admin
passwords are stored as PBKDF2-SHA256 hashes.  Keeping these small primitives
in one module makes it much harder for a future feature to accidentally write a
secret to SQLite in plaintext.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from typing import Union

from cryptography.fernet import Fernet, InvalidToken


class EncryptionError(RuntimeError):
    """Raised when an encrypted tenant token cannot safely be handled."""


class TokenCipher:
    """Fernet wrapper that accepts the deployment's URL-safe base64 key.

    ``Fernet`` includes authenticated encryption, so changing a value in the
    database is detected rather than silently producing a different token.
    """

    def __init__(self, key: Union[str, bytes]):
        if isinstance(key, str):
            key = key.strip().encode("ascii")
        if not key:
            raise EncryptionError("ENCRYPTION_KEY is required to encrypt bot tokens")
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise EncryptionError(
                "ENCRYPTION_KEY must be a valid Fernet 32-byte URL-safe base64 key"
            ) from exc

    @staticmethod
    def generate_key() -> str:
        """Return a new deployment-ready Fernet key (use once as a secret)."""
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, value: str) -> str:
        """Encrypt a token for SQLite storage."""
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        """Decrypt a token read from SQLite, rejecting invalid ciphertext."""
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise EncryptionError(
                "Tenant bot token could not be decrypted. Check ENCRYPTION_KEY; "
                "do not rotate it before re-encrypting existing tokens."
            ) from exc


_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ROUNDS = 260_000


def is_password_hash(value: str) -> bool:
    """Return whether ``value`` is one of this application's password hashes."""
    return bool(value and value.startswith(f"{_PASSWORD_SCHEME}$"))


def hash_password(password: str) -> str:
    """Return a salted, constant-time-verifiable hash for a tenant password."""
    if not password:
        return ""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PASSWORD_ROUNDS
    )
    return "{}${}${}${}".format(
        _PASSWORD_SCHEME,
        _PASSWORD_ROUNDS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(stored_hash: str, submitted: str) -> bool:
    """Verify a PBKDF2 hash without accepting plaintext database values."""
    if not stored_hash or not submitted or not is_password_hash(stored_hash):
        return False
    try:
        scheme, rounds_raw, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if scheme != _PASSWORD_SCHEME:
            return False
        rounds = int(rounds_raw)
        if rounds < 100_000 or rounds > 10_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    except (ValueError, TypeError, binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", submitted.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def mask_secret(value: str) -> str:
    """Return the deliberately non-revealing representation used in HTML."""
    return "***" if value else ""
