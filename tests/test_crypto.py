"""Encryption engine unit tests."""

import pytest


class TestEncryptionEngine:
    """Test EncryptionEngine."""

    def test_disabled_mode(self):
        """No secret → passthrough, no encryption."""
        from engram_core.crypto import EncryptionEngine
        engine = EncryptionEngine(secret=None)
        assert not engine.enabled
        assert engine.encrypt("hello") == "hello"
        assert engine.decrypt("hello") == "hello"

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt should recover original."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test-passphrase")
        original = "sensitive@email.com"
        encrypted = engine.encrypt(original)
        assert encrypted.startswith("enc:v2:")
        assert encrypted != original
        decrypted = engine.decrypt(encrypted)
        assert decrypted == original

    def test_encrypt_idempotent(self):
        """Double-encrypting should not change the value."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test-passphrase")
        encrypted = engine.encrypt("test")
        double_encrypted = engine.encrypt(encrypted)
        assert encrypted == double_encrypted

    def test_wrong_key_returns_ciphertext(self):
        """Wrong key should return original ciphertext, not crash."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine1 = EncryptionEngine(secret="key-1")
        engine2 = EncryptionEngine(secret="key-2")
        encrypted = engine1.encrypt("secret data")
        result = engine2.decrypt(encrypted)
        assert result == encrypted

    def test_encrypt_fields(self):
        """encrypt_fields should only encrypt specified fields."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        data = {"email": "test@test.com", "name": "张三", "role": "developer"}
        encrypted = engine.encrypt_fields(data, {"email"})
        assert encrypted["email"].startswith("enc:v2:")
        assert encrypted["name"] == "张三"
        assert encrypted["role"] == "developer"

    def test_decrypt_fields(self):
        """decrypt_fields should recover encrypted fields."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        data = {"email": "test@test.com", "name": "张三"}
        encrypted = engine.encrypt_fields(data, {"email"})
        decrypted = engine.decrypt_fields(encrypted, {"email"})
        assert decrypted["email"] == "test@test.com"

    def test_empty_string_not_encrypted(self):
        """Empty string should not be encrypted."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        assert engine.encrypt("") == ""

    def test_v1_ciphertext_still_decrypts(self):
        """v1 ciphertext (100k PBKDF2) produced before the v2 upgrade must still decrypt.

        Synthesize a v1 ciphertext by directly running the legacy derivation, then
        verify the engine accepts it and recovers the plaintext.
        """
        from engram_core.crypto import (
            EncryptionEngine,
            ENC_PREFIX_V1,
            HAS_CRYPTO,
            PBKDF2_ITERATIONS_V1,
            _derive_key,
        )
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        secret = "test-passphrase"
        plaintext = "legacy@example.com"
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(secret, salt, PBKDF2_ITERATIONS_V1)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        payload = base64.urlsafe_b64encode(salt + nonce + ciphertext).decode("ascii")
        v1_blob = f"{ENC_PREFIX_V1}{payload}"

        engine = EncryptionEngine(secret=secret)
        assert engine.decrypt(v1_blob) == plaintext

    def test_secret_without_crypto_raises(self):
        """Setting secret without cryptography package must raise RuntimeError."""
        import engram_core.crypto as crypto_mod
        original = crypto_mod.HAS_CRYPTO
        try:
            crypto_mod.HAS_CRYPTO = False
            with pytest.raises(RuntimeError, match="cryptography.*not installed"):
                crypto_mod.EncryptionEngine(secret="test-secret")
        finally:
            crypto_mod.HAS_CRYPTO = original


# ---------------------------------------------------------------------------
# Expanded coverage: v1 → v2 migration, multi-field, Unicode, bad payloads
# ---------------------------------------------------------------------------


class TestEncryptionExpansion:
    """Phase 3.3 — additional coverage beyond the v3.14.0 baseline."""

    def _v1_blob(self, secret: str, plaintext: str) -> str:
        """Produce a real enc:v1: ciphertext using 100k PBKDF2."""
        from engram_core.crypto import (
            ENC_PREFIX_V1,
            PBKDF2_ITERATIONS_V1,
            _derive_key,
        )
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(secret, salt, PBKDF2_ITERATIONS_V1)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        payload = base64.urlsafe_b64encode(salt + nonce + ciphertext).decode("ascii")
        return f"{ENC_PREFIX_V1}{payload}"

    def test_mixed_v1_v2_fields_both_decrypt(self):
        """A dict with v1 in one field and v2 in another must round-trip."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        secret = "rotation-test"
        engine = EncryptionEngine(secret=secret)
        v1_blob = self._v1_blob(secret, "legacy@example.com")
        v2_blob = engine.encrypt("new@example.com")
        data = {"old_email": v1_blob, "new_email": v2_blob, "name": "张三"}
        decrypted = engine.decrypt_fields(data, {"old_email", "new_email"})
        assert decrypted["old_email"] == "legacy@example.com"
        assert decrypted["new_email"] == "new@example.com"
        assert decrypted["name"] == "张三"  # untouched

    def test_v1_decrypt_then_re_encrypt_emits_v2(self):
        """After decrypting a v1 blob and re-encrypting, the new ciphertext is v2."""
        from engram_core.crypto import ENC_PREFIX_V2, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        secret = "upgrade-key"
        engine = EncryptionEngine(secret=secret)
        plaintext = "rotate-me"
        v1_blob = self._v1_blob(secret, plaintext)
        assert engine.decrypt(v1_blob) == plaintext
        # Re-encrypt the decrypted value — must be v2 going forward
        re_encrypted = engine.encrypt(plaintext)
        assert re_encrypted.startswith(ENC_PREFIX_V2)
        # And v2 still round-trips
        assert engine.decrypt(re_encrypted) == plaintext

    def test_already_v2_encrypted_value_is_not_double_encrypted(self):
        """encrypt() must be idempotent — both v1 and v2 inputs are passthrough."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        secret = "idem"
        engine = EncryptionEngine(secret=secret)
        v1_blob = self._v1_blob(secret, "x")
        # encrypt() seeing a v1 blob should pass through unchanged
        assert engine.encrypt(v1_blob) == v1_blob

    def test_unicode_emoji_cjk_roundtrip(self):
        """Encryption must handle multibyte UTF-8 (emoji, CJK, combining chars)."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="unicode-test")
        cases = [
            "简体中文测试",
            "繁體中文測試",
            "🚀✨🔐 emoji string",
            "café résumé naïve",        # combining diacritics
            "Здравствуйте",              # Cyrillic
            "مرحبا بالعالم",            # Arabic (RTL)
            "\u200dzero-width-joiner",  # control char
        ]
        for original in cases:
            ct = engine.encrypt(original)
            assert engine.decrypt(ct) == original, f"failed for {original!r}"

    def test_bad_base64_payload_returns_original(self):
        """Corrupted base64 inside enc:v2: must not crash — return as-is."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        broken = "enc:v2:not_valid_base64!!!"
        # Must not raise; returns the original string
        assert engine.decrypt(broken) == broken

    def test_truncated_ciphertext_returns_original(self):
        """Payload too short to contain salt+nonce must not crash."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        # Valid base64 but too short (needs salt[16]+nonce[12]+at-least-16-tag)
        short = "enc:v2:" + "AAAA"  # 3 bytes after decode
        assert engine.decrypt(short) == short

    def test_unknown_prefix_passthrough(self):
        """Future enc:v9: prefix should pass through (not match any known version)."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        future = "enc:v9:future-format-payload"
        # Doesn't start with v1 or v2 prefix → returned as-is
        assert engine.decrypt(future) == future

    def test_decrypt_fields_skips_non_string_values(self):
        """Non-string values in a dict must be left untouched."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        data = {"email": engine.encrypt("a@b.com"), "age": 42, "tags": ["x"]}
        result = engine.decrypt_fields(data, {"email", "age", "tags"})
        assert result["email"] == "a@b.com"
        assert result["age"] == 42
        assert result["tags"] == ["x"]

    def test_pbkdf2_iteration_constants_are_strict(self):
        """The iteration counts are part of the on-disk contract — pin them."""
        from engram_core.crypto import PBKDF2_ITERATIONS_V1, PBKDF2_ITERATIONS_V2
        # Changing these silently would break decryption of existing data
        assert PBKDF2_ITERATIONS_V1 == 100_000
        assert PBKDF2_ITERATIONS_V2 == 600_000

    def test_default_prefix_is_v2(self):
        """New writes must default to v2 — never accidentally regress to v1."""
        from engram_core.crypto import ENC_PREFIX, ENC_PREFIX_V2
        assert ENC_PREFIX == ENC_PREFIX_V2


class TestDecryptionStrict:
    """v3.14.4 — ``strict=True`` makes decryption failures raise instead of silently
    returning the original ciphertext. Default behavior unchanged."""

    def test_strict_wrong_key_raises(self):
        from engram_core.crypto import DecryptionError, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine_a = EncryptionEngine(secret="key-a")
        engine_b = EncryptionEngine(secret="key-b")
        ct = engine_a.encrypt("secret data")
        with pytest.raises(DecryptionError):
            engine_b.decrypt(ct, strict=True)

    def test_strict_bad_payload_raises(self):
        from engram_core.crypto import DecryptionError, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        with pytest.raises(DecryptionError):
            engine.decrypt("enc:v2:not_valid_base64!!!", strict=True)

    def test_strict_truncated_payload_raises(self):
        from engram_core.crypto import DecryptionError, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        with pytest.raises(DecryptionError):
            engine.decrypt("enc:v2:AAAA", strict=True)

    def test_strict_passthrough_for_unprefixed(self):
        """Non-encrypted values must pass through even in strict mode — not raise."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        # Plain string: not prefixed → returned as-is, no exception
        assert engine.decrypt("plain text", strict=True) == "plain text"

    def test_strict_round_trip_works(self):
        """Happy path: strict mode should not interfere when decryption succeeds."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine = EncryptionEngine(secret="test")
        ct = engine.encrypt("hello")
        assert engine.decrypt(ct, strict=True) == "hello"

    def test_default_mode_unchanged(self):
        """Backward compat: default decrypt with wrong key still returns original."""
        from engram_core.crypto import EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine_a = EncryptionEngine(secret="key-a")
        engine_b = EncryptionEngine(secret="key-b")
        ct = engine_a.encrypt("secret")
        # No strict → still returns the ciphertext on failure (existing behavior)
        assert engine_b.decrypt(ct) == ct

    def test_strict_does_not_leak_cause_chain(self):
        """``raise from None`` is used to hide the original exception's stage,
        which could leak timing-oracle info about where decryption failed."""
        from engram_core.crypto import DecryptionError, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine_a = EncryptionEngine(secret="key-a")
        engine_b = EncryptionEngine(secret="key-b")
        ct = engine_a.encrypt("secret")
        try:
            engine_b.decrypt(ct, strict=True)
        except DecryptionError as exc:
            # __cause__ should be None (suppressed via `from None`)
            assert exc.__cause__ is None
        else:
            pytest.fail("expected DecryptionError")

    def test_decrypt_fields_strict_raises_on_any_failure(self):
        from engram_core.crypto import DecryptionError, EncryptionEngine, HAS_CRYPTO
        if not HAS_CRYPTO:
            pytest.skip("cryptography not installed")
        engine_a = EncryptionEngine(secret="key-a")
        engine_b = EncryptionEngine(secret="key-b")
        bad = engine_a.encrypt("locked")
        data = {"email": bad, "name": "plain"}
        with pytest.raises(DecryptionError):
            engine_b.decrypt_fields(data, {"email"}, strict=True)
        # Input dict must not be mutated (decrypt_fields copies first)
        assert data["email"] == bad
