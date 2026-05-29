"""Tests for corpus encryption — at-rest protection of knowledge content.

When ``ENGRAM_SECRET`` is set AND the ``cryptography`` package is installed,
lessons/decisions/playbooks content fields (summary, detail, question, choice,
reasoning, title, description, outcome) are encrypted on disk while metadata
(id, sensitivity, domain, timestamps) stays plaintext for search/filter.

Design: pre-derived key (PBKDF2 600K + fixed per-engram salt) + per-field
random AES-GCM nonce → fast enough for bulk re-encryption of 200 entries on
every write. Prefix ``enc:v2c:`` distinguishes from per-field ``enc:v2:``
(profile fields).
"""

import json
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_engram(tmp_path: Path):
    engram = tmp_path / "engram"
    identity = engram / "identity"
    identity.mkdir(parents=True)
    (identity / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}),
        encoding="utf-8",
    )
    knowledge = engram / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "decisions.json").write_text("[]", encoding="utf-8")
    return engram


def _make_engram(engram_dir: Path):
    import sys
    sys.path.insert(0, str(_ROOT / "src"))
    from piia_engram.core import Engram
    return Engram(engram_dir)


# ---------------------------------------------------------------------------
# EncryptionEngine corpus methods (unit tests)
# ---------------------------------------------------------------------------


class TestCorpusEncryptDecrypt:
    """Direct tests on EncryptionEngine corpus_encrypt / corpus_decrypt."""

    def test_roundtrip(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret-key")
        salt = os.urandom(16)
        key = engine.derive_corpus_key(salt)

        plaintext = "这是一条重要的经验教训"
        ciphertext = engine.corpus_encrypt(plaintext, key)

        assert ciphertext.startswith("enc:v2c:")
        assert plaintext not in ciphertext
        assert engine.corpus_decrypt(ciphertext, key) == plaintext

    def test_idempotent_encrypt(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret-key")
        key = engine.derive_corpus_key(os.urandom(16))

        plaintext = "test lesson"
        ciphertext = engine.corpus_encrypt(plaintext, key)
        double = engine.corpus_encrypt(ciphertext, key)  # encrypt again
        assert double == ciphertext  # no double-encryption

    def test_plaintext_passthrough_on_decrypt(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret-key")
        key = engine.derive_corpus_key(os.urandom(16))

        plain = "just a normal string"
        assert engine.corpus_decrypt(plain, key) == plain

    def test_empty_passthrough(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret-key")
        key = engine.derive_corpus_key(os.urandom(16))

        assert engine.corpus_encrypt("", key) == ""
        assert engine.corpus_decrypt("", key) == ""

    def test_wrong_key_strict(self):
        from piia_engram.crypto import EncryptionEngine, DecryptionError
        engine = EncryptionEngine("key-one")
        key1 = engine.derive_corpus_key(os.urandom(16))
        ciphertext = engine.corpus_encrypt("secret data", key1)

        engine2 = EncryptionEngine("key-two")
        key2 = engine2.derive_corpus_key(os.urandom(16))

        with pytest.raises(DecryptionError):
            engine2.corpus_decrypt(ciphertext, key2, strict=True)

    def test_wrong_key_lenient(self):
        """Wrong key, strict=False → returns ciphertext as-is (no crash)."""
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("key-one")
        key1 = engine.derive_corpus_key(os.urandom(16))
        ciphertext = engine.corpus_encrypt("secret data", key1)

        engine2 = EncryptionEngine("key-two")
        key2 = engine2.derive_corpus_key(os.urandom(16))

        result = engine2.corpus_decrypt(ciphertext, key2, strict=False)
        assert result == ciphertext  # returns ciphertext, not plaintext

    def test_disabled_engine_passthrough(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine(None)  # no secret → disabled
        assert not engine.enabled

        assert engine.corpus_encrypt("test", b"fake-key") == "test"
        assert engine.corpus_decrypt("test", b"fake-key") == "test"

    def test_key_caching(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-key")
        salt = os.urandom(16)

        key1 = engine.derive_corpus_key(salt)
        key2 = engine.derive_corpus_key(salt)
        assert key1 is key2  # same object — cached

    def test_different_salt_different_key(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-key")
        key1 = engine.derive_corpus_key(os.urandom(16))
        key2 = engine.derive_corpus_key(os.urandom(16))
        assert key1 != key2


# ---------------------------------------------------------------------------
# Entry-level encrypt/decrypt
# ---------------------------------------------------------------------------


class TestEntryEncryptDecrypt:
    """encrypt_entry / decrypt_entry on knowledge dicts."""

    def test_lesson_roundtrip(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        lesson = {
            "id": "abc123",
            "summary": "Python 的 GIL 限制了多线程性能",
            "detail": "使用 multiprocessing 或 asyncio 替代",
            "domain": "python",
            "sensitivity": "work",
            "status": "active",
            "access_count": 3,
        }
        encrypted = engine.encrypt_entry(lesson, key, "lesson")

        # Content fields encrypted
        assert encrypted["summary"].startswith("enc:v2c:")
        assert encrypted["detail"].startswith("enc:v2c:")
        # Metadata plaintext
        assert encrypted["id"] == "abc123"
        assert encrypted["domain"] == "python"
        assert encrypted["sensitivity"] == "work"
        assert encrypted["access_count"] == 3

        # Roundtrip
        decrypted = engine.decrypt_entry(encrypted, key, "lesson")
        assert decrypted["summary"] == lesson["summary"]
        assert decrypted["detail"] == lesson["detail"]

    def test_decision_roundtrip(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        decision = {
            "id": "def456",
            "question": "数据库选型",
            "choice": "SQLite",
            "reasoning": "本地优先，零运维",
            "domain": "architecture",
        }
        encrypted = engine.encrypt_entry(decision, key, "decision")

        assert encrypted["question"].startswith("enc:v2c:")
        assert encrypted["choice"].startswith("enc:v2c:")
        assert encrypted["reasoning"].startswith("enc:v2c:")
        assert encrypted["domain"] == "architecture"

        decrypted = engine.decrypt_entry(encrypted, key, "decision")
        assert decrypted["question"] == decision["question"]
        assert decrypted["choice"] == decision["choice"]
        assert decrypted["reasoning"] == decision["reasoning"]

    def test_playbook_roundtrip(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        playbook = {
            "id": "ghi789",
            "title": "发布流程",
            "description": "PyPI + GitHub Release",
            "outcome": "成功发版",
            "steps": [{"order": 1, "action": "运行测试"}],  # compound, NOT encrypted
            "triggers": ["发布", "release"],
        }
        encrypted = engine.encrypt_entry(playbook, key, "playbook")

        assert encrypted["title"].startswith("enc:v2c:")
        assert encrypted["description"].startswith("enc:v2c:")
        assert encrypted["outcome"].startswith("enc:v2c:")
        # Compound fields NOT encrypted in v1
        assert isinstance(encrypted["steps"], list)
        assert isinstance(encrypted["triggers"], list)

        decrypted = engine.decrypt_entry(encrypted, key, "playbook")
        assert decrypted["title"] == playbook["title"]
        assert decrypted["description"] == playbook["description"]
        assert decrypted["outcome"] == playbook["outcome"]

    def test_mixed_corpus_decrypt(self):
        """Some entries encrypted, some plaintext → transparent."""
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        plain = {"id": "1", "summary": "plain lesson", "detail": ""}
        encrypted = engine.encrypt_entry(
            {"id": "2", "summary": "encrypted lesson", "detail": "detail"},
            key, "lesson",
        )

        d1 = engine.decrypt_entry(plain, key, "lesson")
        d2 = engine.decrypt_entry(encrypted, key, "lesson")

        assert d1["summary"] == "plain lesson"
        assert d2["summary"] == "encrypted lesson"
        assert d2["detail"] == "detail"

    def test_unknown_entry_type_passthrough(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        data = {"id": "x", "summary": "test"}
        result = engine.encrypt_entry(data, key, "unknown_type")
        assert result["summary"] == "test"  # NOT encrypted


# ---------------------------------------------------------------------------
# Integration: Engram core with corpus encryption
# ---------------------------------------------------------------------------


class TestCorpusIntegrationLesson:
    """Lessons encrypted on disk, decrypted in memory."""

    def test_add_lesson_encrypted_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_SECRET", "integration-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_lesson({"summary": "never use eval() in production"})

        # In-memory: plaintext
        assert result["summary"] == "never use eval() in production"

        # On disk: encrypted
        lessons_raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert len(lessons_raw) == 1
        assert lessons_raw[0]["summary"].startswith("enc:v2c:")
        # Metadata plaintext
        assert lessons_raw[0]["id"]
        assert lessons_raw[0]["status"] == "active"

    def test_read_lesson_decrypted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_SECRET", "integration-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        e.add_lesson({"summary": "use pathlib over os.path"})
        lessons = e.get_lessons()

        assert len(lessons) == 1
        assert lessons[0]["summary"] == "use pathlib over os.path"

    def test_no_secret_no_encryption(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_SECRET", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        e.add_lesson({"summary": "plaintext lesson"})

        lessons_raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert lessons_raw[0]["summary"] == "plaintext lesson"

    def test_dedup_works_with_encryption(self, tmp_path, monkeypatch):
        """Dedup compares decrypted summaries, so duplicates are still caught."""
        monkeypatch.setenv("ENGRAM_SECRET", "dedup-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        e.add_lesson({"summary": "always validate user input"})
        result = e.add_lesson({"summary": "always validate user input"})

        assert result.get("status") == "duplicate"

    def test_salt_persisted(self, tmp_path, monkeypatch):
        """Corpus salt is written to .corpus_salt and reused."""
        monkeypatch.setenv("ENGRAM_SECRET", "salt-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        salt_path = engram / ".corpus_salt"
        assert salt_path.exists()
        salt = salt_path.read_bytes()
        assert len(salt) == 16

        # Second Engram instance reuses the same salt
        e2 = _make_engram(engram)
        assert (engram / ".corpus_salt").read_bytes() == salt


class TestCorpusIntegrationDecision:
    """Decisions encrypted on disk, decrypted in memory."""

    def test_add_decision_encrypted_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_SECRET", "decision-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_decision({
            "question": "选择日志框架",
            "choice": "structlog",
            "reasoning": "结构化日志便于分析",
        })

        # In-memory: plaintext
        assert result["question"] == "选择日志框架"
        assert result["choice"] == "structlog"

        # On disk: encrypted
        decisions_raw = json.loads(
            (engram / "knowledge" / "decisions.json").read_text(encoding="utf-8")
        )
        assert decisions_raw[0]["question"].startswith("enc:v2c:")
        assert decisions_raw[0]["choice"].startswith("enc:v2c:")
        assert decisions_raw[0]["reasoning"].startswith("enc:v2c:")


class TestCorpusIntegrationPlaybook:
    """Playbooks encrypted on disk, decrypted in memory."""

    def test_add_playbook_encrypted_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_SECRET", "playbook-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_playbook({
            "title": "发布检查清单",
            "description": "发版前必做的检查步骤",
            "steps": [{"order": 1, "action": "运行测试"}],
            "triggers": ["发布", "release"],
        })

        pb_id = result.get("id")
        assert pb_id

        # On disk: title/description/outcome encrypted, steps plaintext
        pb_raw = json.loads(
            (engram / "playbooks" / f"{pb_id}.json").read_text(encoding="utf-8")
        )
        assert pb_raw["title"].startswith("enc:v2c:")
        assert pb_raw["description"].startswith("enc:v2c:")
        # steps NOT encrypted in v1
        assert isinstance(pb_raw["steps"], list)
        # triggers NOT encrypted (metadata for search)
        assert isinstance(pb_raw["triggers"], list)

    def test_get_playbook_decrypted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_SECRET", "playbook-read-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        added = e.add_playbook({
            "title": "部署流程",
            "description": "Docker 容器部署步骤",
            "steps": [{"order": 1, "action": "docker build"}],
        })
        pb_id = added["id"]

        pb = e.get_playbook(pb_id)
        assert pb["title"] == "部署流程"
        assert pb["description"] == "Docker 容器部署步骤"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestCorpusBackwardCompat:
    """Existing plaintext entries read correctly with encryption enabled."""

    def test_plaintext_entries_read_with_secret(self, tmp_path, monkeypatch):
        """Pre-existing plaintext lessons are readable after enabling ENGRAM_SECRET."""
        engram = _setup_engram(tmp_path)

        # Write plaintext entries (no encryption)
        (engram / "knowledge" / "lessons.json").write_text(
            json.dumps([{
                "id": "old-1",
                "summary": "old plaintext lesson",
                "detail": "legacy detail",
                "domain": "python",
                "timestamp": "2025-01-01T00:00:00",
                "status": "active",
            }]),
            encoding="utf-8",
        )

        # Now enable encryption
        monkeypatch.setenv("ENGRAM_SECRET", "new-secret")
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        lessons = e.get_lessons()
        assert len(lessons) == 1
        assert lessons[0]["summary"] == "old plaintext lesson"
        assert lessons[0]["detail"] == "legacy detail"

    def test_new_entries_encrypted_alongside_old_plaintext(self, tmp_path, monkeypatch):
        """Adding a new lesson encrypts it while old plaintext stays readable."""
        engram = _setup_engram(tmp_path)

        # Seed a plaintext entry
        (engram / "knowledge" / "lessons.json").write_text(
            json.dumps([{
                "id": "old-1",
                "summary": "old lesson",
                "detail": "",
                "domain": "python",
                "timestamp": "2025-01-01T00:00:00",
                "status": "active",
            }]),
            encoding="utf-8",
        )

        monkeypatch.setenv("ENGRAM_SECRET", "migrate-secret")
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        e.add_lesson({"summary": "new encrypted lesson"})

        lessons = e.get_lessons()
        summaries = {l["summary"] for l in lessons}
        assert "old lesson" in summaries
        assert "new encrypted lesson" in summaries

        # On disk: new lesson encrypted, old lesson NOW also encrypted
        # (because add_lesson writes the full list through _write_entries)
        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        encrypted_count = sum(
            1 for r in raw if r.get("summary", "").startswith("enc:v2c:")
        )
        assert encrypted_count == 2  # both entries encrypted after write
