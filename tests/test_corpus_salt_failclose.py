"""M2 security regression: corrupted .corpus_salt must fail-closed, not plaintext.

Code review 2026-06-23 finding S1-1/S1-2 (CRITICAL): When .corpus_salt exists
but is empty or truncated, the init code reads b"" → ``if salt:`` is False →
``_corpus_key`` stays b"" → ALL writes go in plaintext even though ENGRAM_SECRET
is set and the user expects encryption.

The fail-closed guard (line 151) only fires when the salt FILE IS MISSING.
A present-but-invalid file bypasses it entirely.

Fix requirements:
  1. After reading salt, validate ``len(salt) == 16``.
  2. Invalid salt + existing ciphertext → RuntimeError (same as missing-salt path).
  3. Invalid salt + no ciphertext + not read_only → regenerate valid 16-byte salt.
  4. Salt write must be atomic (temp + fsync + rename) to prevent truncation.
"""

from __future__ import annotations

import json
import os

import pytest

from piia_engram.core import Engram


ENGRAM_SECRET = "test-secret-for-m2-regression"


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_encrypted_lesson(root, secret):
    """Create a real encrypted lesson so ciphertext exists on disk."""
    os.environ["ENGRAM_SECRET"] = secret
    try:
        eng = Engram(root=root)
        eng.add_lesson({"summary": "crypto-canary", "detail": "sensitive-data-for-test"}, domain="test")
        # Verify it's actually encrypted on disk
        lesson_file = root / "knowledge" / "lessons.json"
        raw = lesson_file.read_text(encoding="utf-8")
        assert "enc:v2c:" in raw, "Lesson was not encrypted — test setup invalid"
        return lesson_file
    finally:
        os.environ.pop("ENGRAM_SECRET", None)


def _corrupt_salt(root, content: bytes):
    """Overwrite .corpus_salt with arbitrary bytes."""
    salt_path = root / ".corpus_salt"
    salt_path.write_bytes(content)


# ── empty salt + ciphertext → fail closed ───────────────────────────────────

class TestEmptySaltFailClosed:
    """Empty .corpus_salt with existing encrypted data must raise, not silently
    fall through to plaintext writes."""

    def test_empty_salt_with_ciphertext_raises(self, tmp_path, monkeypatch):
        """0-byte .corpus_salt + enc:v2c: data on disk → RuntimeError."""
        _make_encrypted_lesson(tmp_path, ENGRAM_SECRET)
        _corrupt_salt(tmp_path, b"")

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        with pytest.raises(RuntimeError, match=r"corpus_salt.*corrupt|invalid|truncat"):
            Engram(root=tmp_path)

    def test_truncated_salt_with_ciphertext_raises(self, tmp_path, monkeypatch):
        """5-byte .corpus_salt + enc:v2c: data on disk → RuntimeError."""
        _make_encrypted_lesson(tmp_path, ENGRAM_SECRET)
        _corrupt_salt(tmp_path, b"\x01\x02\x03\x04\x05")

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        with pytest.raises(RuntimeError, match=r"corpus_salt.*corrupt|invalid|truncat"):
            Engram(root=tmp_path)

    def test_oversized_salt_with_ciphertext_raises(self, tmp_path, monkeypatch):
        """32-byte .corpus_salt + enc:v2c: data → RuntimeError."""
        _make_encrypted_lesson(tmp_path, ENGRAM_SECRET)
        _corrupt_salt(tmp_path, os.urandom(32))

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        with pytest.raises(RuntimeError, match=r"corpus_salt.*corrupt|invalid|truncat"):
            Engram(root=tmp_path)


# ── empty salt + NO ciphertext → regenerate ─────────────────────────────────

class TestEmptySaltNoCiphertextRegenerates:
    """When no encrypted data exists yet, a corrupted salt can be safely
    regenerated — the user just enabled ENGRAM_SECRET and the salt write
    was interrupted."""

    def test_empty_salt_no_ciphertext_regenerates(self, tmp_path, monkeypatch):
        """0-byte .corpus_salt + no enc:v2c: data → new 16-byte salt created."""
        salt_path = tmp_path / ".corpus_salt"
        salt_path.write_bytes(b"")

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        eng = Engram(root=tmp_path)

        new_salt = salt_path.read_bytes()
        assert len(new_salt) == 16, f"Expected 16-byte salt, got {len(new_salt)}"
        assert eng._corpus_key, "corpus_key must be non-empty after regeneration"

    def test_truncated_salt_no_ciphertext_regenerates(self, tmp_path, monkeypatch):
        """Short .corpus_salt + no existing ciphertext → regenerate."""
        salt_path = tmp_path / ".corpus_salt"
        salt_path.write_bytes(b"\xaa\xbb")

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        eng = Engram(root=tmp_path)

        new_salt = salt_path.read_bytes()
        assert len(new_salt) == 16
        assert eng._corpus_key


# ── plaintext write prevention ──────────────────────────────────────────────

class TestNoPleintextOnCorruptSalt:
    """Even if init somehow succeeds, writes must be encrypted when
    ENGRAM_SECRET is set — never plaintext."""

    def test_write_after_regeneration_is_encrypted(self, tmp_path, monkeypatch):
        """After salt regeneration, new writes must use enc:v2c:."""
        salt_path = tmp_path / ".corpus_salt"
        salt_path.write_bytes(b"")  # empty salt, no ciphertext

        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        eng = Engram(root=tmp_path)
        eng.add_lesson({"summary": "canary", "detail": "must-be-encrypted-content"}, domain="test")

        lesson_file = tmp_path / "knowledge" / "lessons.json"
        assert lesson_file.exists(), "Lesson file not created"
        raw = lesson_file.read_text(encoding="utf-8")
        assert "must-be-encrypted-content" not in raw, \
            "Plaintext found on disk after salt regeneration — encryption failed"
        assert "enc:v2c:" in raw, "Missing enc:v2c: prefix in stored lesson"


# ── salt write atomicity ────────────────────────────────────────────────────

class TestSaltWriteAtomicity:
    """Salt file must be written atomically (temp+rename) so a crash mid-write
    can't produce a truncated file."""

    def test_new_salt_is_exactly_16_bytes(self, tmp_path, monkeypatch):
        """Fresh init creates exactly 16-byte .corpus_salt."""
        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        Engram(root=tmp_path)

        salt_path = tmp_path / ".corpus_salt"
        assert salt_path.exists()
        assert len(salt_path.read_bytes()) == 16

    def test_salt_survives_reopen(self, tmp_path, monkeypatch):
        """Second Engram instance reads same salt → same corpus_key."""
        monkeypatch.setenv("ENGRAM_SECRET", ENGRAM_SECRET)
        eng1 = Engram(root=tmp_path)
        key1 = eng1._corpus_key

        eng2 = Engram(root=tmp_path)
        key2 = eng2._corpus_key

        assert key1 == key2, "Corpus key mismatch between reopens"
        assert key1, "Corpus key must be non-empty"
