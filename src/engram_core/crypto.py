"""Engram field-level encryption engine.

Uses AES-GCM 256 symmetric encryption.
Key is derived from user passphrase (ENGRAM_SECRET) via PBKDF2 + random salt.
Encrypted values are prefixed with "enc:v1:" to distinguish from plaintext.

Requires the `cryptography` package (optional dependency):
    pip install piia-engram[secure]
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

# Encryption prefix marker
ENC_PREFIX = "enc:v1:"

# Try importing cryptography (optional dependency)
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def _derive_key(secret: str, salt: bytes) -> bytes:
    """Derive a 256-bit AES key from passphrase (PBKDF2-SHA256, 100000 rounds)."""
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 100_000)


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
        """Encrypt a string value. Already-encrypted values are returned as-is."""
        if not self.enabled or not value or value.startswith(ENC_PREFIX):
            return value
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(self._secret, salt)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        # Format: enc:v1:<base64(salt + nonce + ciphertext)>
        payload = base64.urlsafe_b64encode(salt + nonce + ciphertext).decode("ascii")
        return f"{ENC_PREFIX}{payload}"

    def decrypt(self, value: str) -> str:
        """Decrypt a string value. Non-encrypted values are returned as-is."""
        if not self.enabled or not isinstance(value, str) or not value.startswith(ENC_PREFIX):
            return value
        try:
            payload = base64.urlsafe_b64decode(value[len(ENC_PREFIX):])
            salt = payload[:16]
            nonce = payload[16:28]
            ciphertext = payload[28:]
            key = _derive_key(self._secret, salt)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as exc:
            # Decryption failed (wrong key, corrupt data) — return original, don't crash
            print(f"[engram] decryption failed: {exc}", file=sys.stderr)
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

    def decrypt_fields(self, data: dict, fields: set[str]) -> dict:
        """Decrypt specified fields in a dictionary."""
        if not self.enabled or not fields:
            return data
        result = dict(data)
        for field in fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.decrypt(result[field])
        return result
