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


# ---------------------------------------------------------------------------
# Regression: write-back paths must not leak plaintext to disk
# ---------------------------------------------------------------------------


class TestNoPlaintextLeakOnWriteBack:
    """Audit-driven tests: any code path that reads decrypted entries and
    writes them back MUST re-encrypt. These tests verify specific write-back
    sites that were identified during code audit.
    """

    def test_promote_knowledge_preserves_encryption(self, tmp_path, monkeypatch):
        """reports_review.promote_knowledge must re-encrypt after promoting."""
        monkeypatch.setenv("ENGRAM_SECRET", "promote-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        # Add a staging lesson
        result = e.add_lesson({
            "summary": "secret staging lesson",
            "tier": "staging",
        })
        lesson_id = result["id"]

        # Verify it's encrypted on disk
        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw[0]["summary"].startswith("enc:v2c:")

        # Promote it
        promote_result = e.promote_knowledge(lesson_id)
        assert promote_result.get("status") == "promoted"

        # After promotion, content MUST still be encrypted on disk
        raw_after = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw_after[0]["summary"].startswith("enc:v2c:"), \
            "promote_knowledge leaked plaintext to disk!"
        assert raw_after[0]["tier"] == "verified"

    def test_evaluate_tiers_preserves_encryption(self, tmp_path, monkeypatch):
        """retrieval.evaluate_tiers must re-encrypt after auto-promoting."""
        monkeypatch.setenv("ENGRAM_SECRET", "tier-eval-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        # Add a staging lesson with enough access_count to trigger promotion
        e.add_lesson({
            "summary": "auto-promote candidate",
            "tier": "staging",
            "access_count": 5,  # above threshold of 3
        })

        # Verify encrypted on disk
        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw[0]["summary"].startswith("enc:v2c:")

        # Run tier evaluation
        result = e.evaluate_tiers()
        assert result["promoted"] == 1

        # After evaluation, content MUST still be encrypted
        raw_after = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw_after[0]["summary"].startswith("enc:v2c:"), \
            "evaluate_tiers leaked plaintext to disk!"
        assert raw_after[0]["tier"] == "verified"

    def test_review_knowledge_preserves_encryption(self, tmp_path, monkeypatch):
        """core.review_knowledge must re-encrypt after marking as reviewed."""
        monkeypatch.setenv("ENGRAM_SECRET", "review-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_lesson({"summary": "reviewable lesson"})
        lesson_id = result["id"]

        e.review_knowledge(lesson_id)

        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw[0]["summary"].startswith("enc:v2c:"), \
            "review_knowledge leaked plaintext to disk!"

    def test_update_knowledge_preserves_encryption(self, tmp_path, monkeypatch):
        """core.update_lesson must re-encrypt when updating metadata."""
        monkeypatch.setenv("ENGRAM_SECRET", "update-test-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_lesson({"summary": "updatable lesson", "domain": "python"})
        lesson_id = result["id"]

        e.update_lesson(lesson_id, {"domain": "devops"})

        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        target = [r for r in raw if r["id"] == lesson_id][0]
        assert target["summary"].startswith("enc:v2c:"), \
            "update_lesson leaked plaintext to disk!"
        assert target["domain"] == "devops"


# ---------------------------------------------------------------------------
# Codex audit regression tests — derived-data plaintext leak prevention
# ---------------------------------------------------------------------------

# Unique marker that we search for in raw file bytes to detect plaintext leaks
_MARKER = "CODEX_A5_PLAINTEXT_PROBE_"


def _scan_root_for_marker(root: Path, marker: str) -> list[str]:
    """Scan ALL files under root for the marker string (case-insensitive).

    Returns list of relative paths where the marker was found.
    """
    marker_bytes = marker.lower().encode("utf-8")
    hits = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes().lower()
            if marker_bytes in data:
                hits.append(str(path.relative_to(root)))
        except OSError:
            continue
    return hits


class TestCodexAuditRegressions:
    """Regression tests for all Codex a5 audit FAIL findings.

    Each test writes data with unique markers, then scans ALL files under
    the engram root to verify no plaintext marker appears on disk.
    """

    def test_playbook_index_no_plaintext_title(self, tmp_path, monkeypatch):
        """#1: _index.json must not contain plaintext playbook title."""
        monkeypatch.setenv("ENGRAM_SECRET", "index-title-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        title = f"{_MARKER}INDEX_TITLE"
        e.add_playbook({
            "title": title,
            "steps": [{"order": 1, "action": "test"}],
            "triggers": ["test"],
        })

        # _index.json must NOT contain the plaintext title
        index_raw = (engram / "playbooks" / "_index.json").read_bytes()
        assert title.encode() not in index_raw, \
            "_index.json contains plaintext title!"
        # But in-memory read must return plaintext
        index = e._read_playbook_index()
        titles = [entry.get("title", "") for entry in index]
        assert title in titles

    def test_hybrid_search_index_no_plaintext_corpus(self, tmp_path, monkeypatch):
        """#2: search_index.db must not materialise decrypted content."""
        monkeypatch.setenv("ENGRAM_SECRET", "hybrid-index-key")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        marker = f"{_MARKER}HYBRID_LESSON"
        e.add_lesson({"summary": marker})

        # Trigger search to potentially build hybrid index
        e.search_knowledge("test")

        # search_index.db must not exist or must not contain our marker
        db_path = engram / "search_index.db"
        if db_path.exists():
            data = db_path.read_bytes().lower()
            assert marker.lower().encode() not in data, \
                "search_index.db contains plaintext corpus content!"

    def test_execution_plan_no_plaintext(self, tmp_path, monkeypatch):
        """#3: execution plan must not contain plaintext title or step content."""
        monkeypatch.setenv("ENGRAM_SECRET", "exec-plan-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        title = f"{_MARKER}EXEC_TITLE"
        action = f"{_MARKER}EXEC_STEP_ACTION"
        result = e.add_playbook({
            "title": title,
            "steps": [{"order": 1, "action": action, "detail": "detail"}],
            "triggers": ["exec"],
        })
        pb_id = result["id"]

        # Prepare execution → writes execution plan to disk
        e.prepare_playbook_execution(pb_id)

        exec_path = engram / "playbooks" / "executions" / f"{pb_id}.json"
        assert exec_path.exists()
        raw = exec_path.read_bytes()
        assert title.encode() not in raw, \
            "Execution plan contains plaintext title!"
        assert action.encode() not in raw, \
            "Execution plan contains plaintext step action!"

        # get_execution_status must return decrypted values
        status = e.get_execution_status(pb_id)
        assert status.get("title") == title

    def test_playbook_steps_encrypted_on_disk(self, tmp_path, monkeypatch):
        """#4: Playbook steps action/detail must be encrypted on disk."""
        monkeypatch.setenv("ENGRAM_SECRET", "steps-enc-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        action_marker = f"{_MARKER}STEP_ACTION"
        detail_marker = f"{_MARKER}STEP_DETAIL"
        pitfall_marker = f"{_MARKER}PITFALL"
        result = e.add_playbook({
            "title": "test playbook",
            "steps": [
                {"order": 1, "action": action_marker, "detail": detail_marker},
            ],
            "pitfalls": [pitfall_marker],
            "triggers": ["test"],
        })
        pb_id = result["id"]

        # On disk: steps and pitfalls must be encrypted
        pb_raw = (engram / "playbooks" / f"{pb_id}.json").read_bytes()
        assert action_marker.encode() not in pb_raw, \
            "Playbook file contains plaintext step action!"
        assert detail_marker.encode() not in pb_raw, \
            "Playbook file contains plaintext step detail!"
        assert pitfall_marker.encode() not in pb_raw, \
            "Playbook file contains plaintext pitfall!"

        # In-memory: get_playbook must return decrypted steps
        pb = e.get_playbook(pb_id)
        assert pb["steps"][0]["action"] == action_marker
        assert pb["steps"][0]["detail"] == detail_marker
        assert pb["pitfalls"][0] == pitfall_marker

    def test_export_import_roundtrip_across_roots(self, tmp_path, monkeypatch):
        """#5: Export+import to a different root with same passphrase must
        produce readable (not ciphertext) data in the destination."""
        passphrase = "cross-root-key"

        # Root A: create data
        root_a = tmp_path / "root_a"
        engram_a = _setup_engram(root_a)
        monkeypatch.setenv("ENGRAM_SECRET", passphrase)
        monkeypatch.setenv("ENGRAM_DIR", str(engram_a))
        ea = _make_engram(engram_a)

        lesson_text = f"{_MARKER}EXPORT_LESSON"
        ea.add_lesson({"summary": lesson_text})

        # Export from root A
        export_path = tmp_path / "backup.json"
        ea.export_all(str(export_path))

        # Verify export contains plaintext (not ciphertext)
        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        exported_summaries = [
            l.get("summary", "") for l in export_data["knowledge"]["lessons"]
        ]
        assert lesson_text in exported_summaries, \
            "Export file contains ciphertext instead of plaintext!"

        # Root B: import
        root_b = tmp_path / "root_b"
        engram_b = _setup_engram(root_b)
        monkeypatch.setenv("ENGRAM_DIR", str(engram_b))
        eb = _make_engram(engram_b)

        eb.import_all(str(export_path))

        # Root B must have readable data
        lessons = eb.get_lessons()
        summaries = [l["summary"] for l in lessons]
        assert lesson_text in summaries, \
            "Imported lesson is not readable in the new root!"
        # And it should be encrypted on disk in root B
        raw = json.loads(
            (engram_b / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        enc_count = sum(1 for r in raw if r.get("summary", "").startswith("enc:v2c:"))
        assert enc_count > 0, "Imported data not encrypted on disk in root B!"

    def test_missing_salt_with_ciphertext_fails_closed(self, tmp_path, monkeypatch):
        """#6: Deleting .corpus_salt when encrypted data exists must raise,
        not silently create a new salt."""
        monkeypatch.setenv("ENGRAM_SECRET", "fail-closed-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        # Write encrypted data
        e.add_lesson({"summary": "encrypted lesson for salt test"})

        # Verify data is encrypted on disk
        raw = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert raw[0]["summary"].startswith("enc:v2c:")

        # Delete the salt
        salt_path = engram / ".corpus_salt"
        assert salt_path.exists()
        salt_path.unlink()

        # Attempting to create a new Engram must fail, not silently create new salt
        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram.core import Engram
        with pytest.raises(RuntimeError, match="corpus_salt.*missing"):
            Engram(engram)

    def test_full_root_scan_no_plaintext_markers(self, tmp_path, monkeypatch):
        """Comprehensive: after writing lessons/decisions/playbooks and
        triggering all derived-write paths, scan EVERY file under root
        for any plaintext marker. This is the master no-leak invariant."""
        monkeypatch.setenv("ENGRAM_SECRET", "full-scan-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        # Write data with unique markers
        lesson_marker = f"{_MARKER}FULL_LESSON"
        decision_marker = f"{_MARKER}FULL_DECISION"
        pb_title_marker = f"{_MARKER}FULL_PB_TITLE"
        step_marker = f"{_MARKER}FULL_STEP"

        e.add_lesson({"summary": lesson_marker})
        e.add_decision({"question": decision_marker, "choice": "c"})
        pb = e.add_playbook({
            "title": pb_title_marker,
            "steps": [{"order": 1, "action": step_marker}],
            "triggers": ["test"],
        })

        # Trigger derived writes
        if pb.get("id"):
            e.prepare_playbook_execution(pb["id"])

        # Scan everything
        for marker in (lesson_marker, decision_marker,
                       pb_title_marker, step_marker):
            hits = _scan_root_for_marker(engram, marker)
            assert not hits, \
                f"Plaintext marker '{marker}' found in: {hits}"


class TestCodexRound2Regressions:
    """Regression tests for the 4 P1 findings from Codex's a5 round-2 re-audit.

    P1-1: rebuild_index()/CLI reindex still wrote decrypted bodies to
          search_index.db even with corpus encryption on.
    P1-2: a plaintext search_index.db left from a pre-encryption run was never
          purged when encryption was later enabled.
    P1-3: .corpus_salt-missing fail-closed detection only scanned the first 4KB,
          so ciphertext past that window was missed and a fresh salt minted.
    P1-4: update_execution_step(notes=...) wrote the note in cleartext into the
          execution plan.
    """

    def test_rebuild_index_refuses_or_purges_when_corpus_encrypted(
        self, tmp_path, monkeypatch
    ):
        """P1-1: explicit reindex must not materialise plaintext into the db."""
        monkeypatch.setenv("ENGRAM_SECRET", "reindex-refuse-key")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        marker = f"{_MARKER}REINDEX_LESSON"
        e.add_lesson({"summary": marker})

        result = e.rebuild_index()
        # Must report it skipped the build because of corpus encryption.
        assert result.get("skipped") == "corpus_encrypted", result
        assert result.get("indexed") == 0, result

        db_path = engram / "search_index.db"
        if db_path.exists():
            data = db_path.read_bytes().lower()
            assert marker.lower().encode() not in data, \
                "reindex materialised plaintext corpus into search_index.db!"

    def test_enabling_encryption_purges_existing_plaintext_search_index(
        self, tmp_path, monkeypatch
    ):
        """P1-2: a plaintext index from a pre-encryption run must be purged
        when the engram is next opened with encryption enabled."""
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))

        # Phase 1: NO secret — build a plaintext hybrid index on disk.
        monkeypatch.delenv("ENGRAM_SECRET", raising=False)
        e_plain = _make_engram(engram)
        marker = f"{_MARKER}STALE_INDEX_LESSON"
        e_plain.add_lesson({"summary": marker})
        built = e_plain.rebuild_index()
        db_path = engram / "search_index.db"
        # Sanity: the plaintext index exists and contains the marker.
        if not (db_path.exists() and built.get("indexed", 0) > 0):
            pytest.skip("hybrid backend unavailable; cannot build plaintext index")
        assert marker.lower().encode() in db_path.read_bytes().lower(), \
            "precondition failed: plaintext index did not contain marker"

        # Phase 2: re-open WITH a secret — init must purge the stale index.
        monkeypatch.setenv("ENGRAM_SECRET", "now-encrypted-key")
        _make_engram(engram)
        assert not db_path.exists(), \
            "stale plaintext search_index.db survived enabling encryption!"

    def test_missing_salt_detects_ciphertext_after_4kb(self, tmp_path, monkeypatch):
        """P1-3: ciphertext located past the first 4KB of a corpus file must
        still trigger the fail-closed salt-missing guard."""
        monkeypatch.setenv("ENGRAM_SECRET", "late-ciphertext-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))

        # Craft a lessons.json whose first >4KB are plaintext padding, with a
        # real corpus-ciphertext token only AFTER the 4KB scan window.
        padding = [
            {"id": f"pad{i}", "type": "lesson", "status": "active",
             "summary": "X" * 200}
            for i in range(40)  # ~ >8KB of leading plaintext
        ]
        late_entry = {
            "id": "late", "type": "lesson", "status": "active",
            "summary": "enc:v2c:QUtFRF9DSVBIRVJURVhUX1RPS0VO",
        }
        lessons = padding + [late_entry]
        lessons_path = engram / "knowledge" / "lessons.json"
        lessons_path.write_text(json.dumps(lessons), encoding="utf-8")
        # The ciphertext marker must indeed sit past the old 4KB window.
        assert lessons_path.read_bytes().find(b"enc:v2c:") > 4096

        # No salt present → opening must fail-closed (not mint a new salt).
        assert not (engram / ".corpus_salt").exists()
        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram.core import Engram
        with pytest.raises(RuntimeError, match="corpus_salt.*missing"):
            Engram(engram)

    def test_update_execution_step_encrypts_notes(self, tmp_path, monkeypatch):
        """P1-4: notes passed to update_execution_step must be encrypted on
        disk and round-trip back through get_execution_status."""
        monkeypatch.setenv("ENGRAM_SECRET", "exec-notes-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        result = e.add_playbook({
            "title": "notes playbook",
            "steps": [{"order": 1, "action": "do it", "detail": "carefully"}],
            "triggers": ["notes"],
        })
        pb_id = result["id"]
        e.prepare_playbook_execution(pb_id)

        notes_marker = f"{_MARKER}STEP_NOTES"
        upd = e.update_execution_step(pb_id, 1, "failed", notes=notes_marker)
        assert upd.get("status") == "updated", upd

        exec_path = engram / "playbooks" / "executions" / f"{pb_id}.json"
        raw = exec_path.read_bytes()
        assert notes_marker.encode() not in raw, \
            "update_execution_step wrote notes in cleartext!"

        # get_execution_status must return the decrypted note.
        status = e.get_execution_status(pb_id)
        step = next(s for s in status["steps"] if s.get("order") == 1)
        assert step.get("notes") == notes_marker
        assert step.get("status") == "failed"

    def test_full_root_scan_after_reindex_and_step_update(
        self, tmp_path, monkeypatch
    ):
        """Master no-leak invariant covering the round-2 derived-write paths:
        explicit reindex + execution step updates (incl. notes)."""
        monkeypatch.setenv("ENGRAM_SECRET", "r2-full-scan-key")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        lesson_marker = f"{_MARKER}R2_LESSON"
        pb_title_marker = f"{_MARKER}R2_PB_TITLE"
        step_marker = f"{_MARKER}R2_STEP"
        notes_marker = f"{_MARKER}R2_NOTES"

        e.add_lesson({"summary": lesson_marker})
        pb = e.add_playbook({
            "title": pb_title_marker,
            "steps": [{"order": 1, "action": step_marker}],
            "triggers": ["r2"],
        })
        pb_id = pb["id"]
        e.prepare_playbook_execution(pb_id)
        e.update_execution_step(pb_id, 1, "completed", notes=notes_marker)

        # Explicit reindex (must refuse/purge under encryption).
        e.rebuild_index()

        for marker in (lesson_marker, pb_title_marker, step_marker, notes_marker):
            hits = _scan_root_for_marker(engram, marker)
            assert not hits, \
                f"Plaintext marker '{marker}' found in: {hits}"


class TestCodexRound3Hardening:
    """Regression tests for the 4 non-blocking hardening items (O1-O4) from
    Codex's a5 round-3 re-audit. Each test is a negative control: it must FAIL
    on the pre-fix code (commit f9b489e) and pass after the round-3 hardening.

    O1: _has_existing_ciphertext() skipped playbooks/_index.json, so a root
        whose only surviving ciphertext was the index minted a fresh salt.
    O2: purge_search_index() swallowed unlink failures, so an un-removable
        plaintext index under encryption was silently left readable.
    O3: CLI reindex printed "[ok] reindexed 0 entries" under encryption instead
        of saying the index was skipped/purged.
    O4: _ensure_index_fresh() had no _corpus_encrypted() guard of its own, so a
        direct internal call would materialise a plaintext search_index.db.
    """

    def test_missing_salt_detects_ciphertext_in_playbook_index(
        self, tmp_path, monkeypatch
    ):
        """O1: ciphertext present ONLY in playbooks/_index.json must still
        trigger the fail-closed salt-missing guard."""
        monkeypatch.setenv("ENGRAM_SECRET", "index-only-cipher-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))

        # Knowledge files are plaintext/empty; the ONLY corpus ciphertext lives
        # in the playbook index. Old code skipped _index.json → missed it.
        pb_dir = engram / "playbooks"
        pb_dir.mkdir(parents=True, exist_ok=True)
        (pb_dir / "_index.json").write_text(
            json.dumps([{
                "id": "pb1", "status": "active",
                "title": "enc:v2c:QUtFRF9JTkRFWF9USVRMRV9UT0tFTg==",
            }]),
            encoding="utf-8",
        )
        # Sanity: no other corpus file carries the marker.
        assert b"enc:v2c:" not in (engram / "knowledge" / "lessons.json").read_bytes()
        assert not (engram / ".corpus_salt").exists()

        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram.core import Engram
        with pytest.raises(RuntimeError, match="corpus_salt.*missing"):
            Engram(engram)

    def test_purge_search_index_fails_closed_if_db_survives(
        self, tmp_path, monkeypatch
    ):
        """O2: if a stale plaintext search_index.db can't be removed under
        encryption, init must fail-closed instead of leaving it readable."""
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        # A stale plaintext index file is present and (simulated) un-removable.
        (engram / "search_index.db").write_bytes(b"PLAINTEXT_INDEX_BODY")

        real_unlink = Path.unlink

        def _no_unlink_index(self, *args, **kwargs):
            if self.name == "search_index.db":
                raise PermissionError("simulated lock")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", _no_unlink_index)
        monkeypatch.setenv("ENGRAM_SECRET", "purge-failclosed-key")

        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram.core import Engram
        with pytest.raises(RuntimeError, match="search_index.db"):
            Engram(engram)

    def test_cli_reindex_reports_corpus_encrypted_skip(
        self, tmp_path, monkeypatch, capsys
    ):
        """O3: CLI reindex under encryption must report skip/purge, not the
        misleading '[ok] reindexed 0 entries'."""
        monkeypatch.setenv("ENGRAM_SECRET", "cli-reindex-key")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))

        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram import setup_wizard
        setup_wizard._run_reindex()

        out = capsys.readouterr().out.lower()
        assert "encryption" in out, out
        assert "reindexed 0 entries" not in out, out

    def test_ensure_index_fresh_noops_when_corpus_encrypted(
        self, tmp_path, monkeypatch
    ):
        """O4: a direct call to the sink-adjacent helper must not materialise a
        persistent search_index.db while corpus encryption is active."""
        monkeypatch.setenv("ENGRAM_SECRET", "ensure-fresh-key")
        monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        e.add_lesson({"summary": f"{_MARKER}O4_LESSON"})
        db_path = engram / "search_index.db"
        if db_path.exists():
            db_path.unlink()

        # Defense-in-depth: calling the helper directly under encryption.
        e._ensure_index_fresh(e._all_indexable_entries())
        assert not db_path.exists(), \
            "_ensure_index_fresh materialised search_index.db under corpus encryption!"
