"""Engram field-level encryption engine.

Uses AES-GCM 256 symmetric encryption.
Key is derived from user passphrase (ENGRAM_SECRET) via PBKDF2-SHA256 + random salt.

Two encryption versions are supported on disk:
- ``enc:v1:`` — legacy 100,000 PBKDF2 iterations (still decrypted, never re-emitted).
- ``enc:v2:`` — current 600,000 PBKDF2 iterations (matches OWASP 2023+ guidance).

New writes always use ``enc:v2:``.  When a v1 ciphertext is decrypted and later
re-encrypted (e.g. when the user updates the field), it is upgraded to v2.

Requires the `cryptography` package (optional dependency):
    pip install piia-engram[secure]
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)


class DecryptionError(Exception):
    """Raised when ``EncryptionEngine.decrypt(..., strict=True)`` cannot recover
    the plaintext — typically wrong key, corrupted payload, or truncated data.

    The default ``decrypt(value)`` call still returns the original ciphertext
    on failure (with a logger warning) for backward compatibility, but callers
    that need explicit error handling should pass ``strict=True``.
    """


# Encryption prefix markers — version-aware on disk for forward upgrades
ENC_PREFIX_V1 = "enc:v1:"
ENC_PREFIX_V2 = "enc:v2:"
ENC_PREFIX = ENC_PREFIX_V2  # default for new writes
ENC_PREFIXES = (ENC_PREFIX_V2, ENC_PREFIX_V1)

# PBKDF2 iteration counts per version
PBKDF2_ITERATIONS_V1 = 100_000   # legacy, decrypt-only
PBKDF2_ITERATIONS_V2 = 600_000   # OWASP 2023+ recommended floor

# Try importing cryptography (optional dependency)
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def _derive_key(secret: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS_V2) -> bytes:
    """Derive a 256-bit AES key from passphrase (PBKDF2-SHA256)."""
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)


def _starts_with_enc_prefix(value: str) -> bool:
    return any(value.startswith(p) for p in ENC_PREFIXES)


class EncryptionEngine:
    """Field-level encrypt/decrypt engine."""

    def __init__(self, secret: str | None = None):
        """Initialize. If secret is None, encryption is disabled (passthrough)."""
        self._secret = secret or ""
        self.enabled = bool(secret) and HAS_CRYPTO
        if secret and not HAS_CRYPTO:
            raise RuntimeError(
                "ENGRAM_SECRET is set but 'cryptography' package is not installed. "
                "Data would be stored in plaintext despite user expectation of encryption. "
                "Install it: pip install piia-engram[secure]"
            )

    def encrypt(self, value: str) -> str:
        """Encrypt a string value. Already-encrypted values are returned as-is.

        New writes always use ENC_PREFIX_V2 (600k PBKDF2 iterations).
        """
        if not self.enabled or not value or _starts_with_enc_prefix(value):
            return value
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(self._secret, salt, PBKDF2_ITERATIONS_V2)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        # Format: enc:v2:<base64(salt + nonce + ciphertext)>
        payload = base64.urlsafe_b64encode(salt + nonce + ciphertext).decode("ascii")
        return f"{ENC_PREFIX_V2}{payload}"

    def decrypt(self, value: str, *, strict: bool = False) -> str:
        """Decrypt a string value. Non-encrypted values are returned as-is.

        Supports both ``enc:v1:`` (100k PBKDF2) and ``enc:v2:`` (600k PBKDF2) on input.

        Args:
            value: The string to decrypt. Non-string or non-prefixed values pass through.
            strict: When ``True``, raise :class:`DecryptionError` if a prefixed
                value cannot be decrypted (wrong key, truncated, corrupted).
                When ``False`` (default — backward compatible), log a warning
                and return the original ciphertext so callers don't crash.

        ⚠ The default behavior can mask wrong-key bugs: if the caller doesn't
        check whether the return value still has an ``enc:`` prefix, they may
        treat ciphertext as plaintext. New code should prefer ``strict=True``
        and catch :class:`DecryptionError` explicitly.
        """
        if not self.enabled or not isinstance(value, str):
            return value
        if value.startswith(ENC_PREFIX_V2):
            prefix, iterations = ENC_PREFIX_V2, PBKDF2_ITERATIONS_V2
        elif value.startswith(ENC_PREFIX_V1):
            prefix, iterations = ENC_PREFIX_V1, PBKDF2_ITERATIONS_V1
        else:
            return value
        try:
            payload = base64.urlsafe_b64decode(value[len(prefix):])
            salt = payload[:16]
            nonce = payload[16:28]
            ciphertext = payload[28:]
            key = _derive_key(self._secret, salt, iterations)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as exc:
            if strict:
                # Don't include exc as __cause__ to avoid leaking timing/oracle
                # information about which stage failed (b64, key derivation, AEAD tag).
                raise DecryptionError(
                    f"decryption failed for {prefix} payload (wrong key or corrupted data)"
                ) from None
            # Backward-compatible fallback: log + return original ciphertext.
            # Callers that don't validate the prefix after this call may treat
            # ciphertext as plaintext — prefer strict=True in new code.
            logger.warning("decryption failed: %s", exc)
            return value

    def encrypt_fields(self, data: dict, fields: set[str]) -> dict:
        """Encrypt specified fields in a dictionary."""
        if not self.enabled or not fields:
            return data
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.encrypt(result[field])
        return result

    def decrypt_fields(self, data: dict, fields: set[str], *, strict: bool = False) -> dict:
        """Decrypt specified fields in a dictionary.

        Args:
            data: The dictionary holding the encrypted values.
            fields: Set of field names to attempt decryption on.
            strict: Passed through to :meth:`decrypt`. When ``True``, the first
                failing field raises :class:`DecryptionError` and processing stops
                (no partial mutation — the input is copied first).
        """
        if not self.enabled or not fields:
            return data
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.decrypt(result[field], strict=strict)
        return result
