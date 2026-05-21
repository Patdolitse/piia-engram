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
        assert encrypted.startswith("enc:v1:")
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
        assert encrypted["email"].startswith("enc:v1:")
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
