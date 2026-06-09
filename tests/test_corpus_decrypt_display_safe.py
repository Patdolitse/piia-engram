"""Tests for display-safe handling of failed corpus decryption.

Audit finding ("crypto 静默解密"): when corpus decryption silently fails (wrong
or missing ``ENGRAM_SECRET``, corrupted payload), the read path returned the
raw ciphertext, which the model could then treat as if it were plaintext
content. The fix substitutes a clear placeholder
(:data:`DECRYPT_FAILED_PLACEHOLDER`) at the *model-facing* read boundaries,
while keeping the raw ciphertext untouched on disk so a later correct-key read
fully recovers the original content (no data loss).

The single most important property proven here is the data-safety one
(``test_disk_ciphertext_preserved_after_wrong_key_read``): a wrong-key read —
including its access-count write-back — must NOT destroy the recoverable
ciphertext on disk.
"""

import json
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


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
# Unit: EncryptionEngine.sanitize_failed_decryption
# ---------------------------------------------------------------------------


class TestSanitizeFailedDecryption:
    def test_leaked_ciphertext_replaced_with_placeholder(self):
        from piia_engram.crypto import (
            EncryptionEngine,
            DECRYPT_FAILED_PLACEHOLDER,
        )
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))

        # Simulate a leaked-through ciphertext field (decrypt failed upstream).
        ciphertext = engine.corpus_encrypt("top secret detail", key)
        leaked = {"id": "1", "summary": ciphertext, "detail": ciphertext,
                  "domain": "python"}

        view = engine.sanitize_failed_decryption(leaked, "lesson")
        assert view["summary"] == DECRYPT_FAILED_PLACEHOLDER
        assert view["detail"] == DECRYPT_FAILED_PLACEHOLDER
        # Metadata untouched
        assert view["domain"] == "python"
        # Original object NOT mutated (fresh copy)
        assert leaked["summary"] == ciphertext

    def test_plaintext_entry_unchanged(self):
        from piia_engram.crypto import EncryptionEngine
        engine = EncryptionEngine("test-secret")

        entry = {"id": "1", "summary": "正常明文", "detail": "plain detail"}
        view = engine.sanitize_failed_decryption(entry, "lesson")
        assert view["summary"] == "正常明文"
        assert view["detail"] == "plain detail"

    def test_compound_playbook_step_sanitized(self):
        from piia_engram.crypto import (
            EncryptionEngine,
            DECRYPT_FAILED_PLACEHOLDER,
        )
        engine = EncryptionEngine("test-secret")
        key = engine.derive_corpus_key(os.urandom(16))
        ct = engine.corpus_encrypt("leaked step action", key)

        pb = {
            "id": "p1",
            "title": ct,
            "description": "plain desc",
            "steps": [{"order": 1, "action": ct, "detail": "ok"}],
            "pitfalls": [ct, "a real pitfall"],
        }
        view = engine.sanitize_failed_decryption(pb, "playbook")
        assert view["title"] == DECRYPT_FAILED_PLACEHOLDER
        assert view["description"] == "plain desc"
        assert view["steps"][0]["action"] == DECRYPT_FAILED_PLACEHOLDER
        assert view["steps"][0]["detail"] == "ok"
        assert view["pitfalls"][0] == DECRYPT_FAILED_PLACEHOLDER
        assert view["pitfalls"][1] == "a real pitfall"


# ---------------------------------------------------------------------------
# Integration: wrong-key reads never surface ciphertext to the model
# ---------------------------------------------------------------------------


class TestWrongKeyReadIsDisplaySafe:
    def _write_encrypted_lesson(self, tmp_path, monkeypatch, summary, detail):
        """Create an engram with the RIGHT key and store one encrypted lesson."""
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": summary, "detail": detail})
        return engram

    def test_get_lessons_wrong_key_returns_placeholder_not_ciphertext(
        self, tmp_path, monkeypatch
    ):
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        engram = self._write_encrypted_lesson(
            tmp_path, monkeypatch, "secret summary", "secret detail"
        )

        # Re-open with a WRONG key (same persisted salt → derived key differs).
        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        lessons = e2.get_lessons()

        assert len(lessons) == 1
        # Never the real plaintext, never the raw ciphertext.
        assert lessons[0]["summary"] == DECRYPT_FAILED_PLACEHOLDER
        assert lessons[0]["detail"] == DECRYPT_FAILED_PLACEHOLDER
        assert "enc:v2c:" not in lessons[0]["summary"]
        assert "secret summary" not in lessons[0]["summary"]

    def test_search_knowledge_wrong_key_returns_placeholder(
        self, tmp_path, monkeypatch
    ):
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        engram = self._write_encrypted_lesson(
            tmp_path, monkeypatch, "alpha bravo charlie", "delta echo"
        )

        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        # Match on plaintext metadata-independent term won't work (content is
        # ciphertext), so search by a term and confirm: if anything is returned,
        # its content is the placeholder, never ciphertext. We assert no leak.
        res = e2.search_knowledge("enc", scope="lessons", limit=10)
        for item in res.get("lessons", []):
            assert "enc:v2c:" not in json.dumps(item, ensure_ascii=False)
            # any surfaced content field is the placeholder
            if "summary" in item and item["summary"]:
                assert item["summary"] in (
                    DECRYPT_FAILED_PLACEHOLDER,
                ) or not item["summary"].startswith("enc:")

    def test_decision_wrong_key_returns_placeholder(self, tmp_path, monkeypatch):
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_decision({
            "question": "db choice", "choice": "sqlite",
            "reasoning": "local first",
        })

        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        decisions = e2.get_decisions()
        assert len(decisions) == 1
        assert decisions[0]["choice"] == DECRYPT_FAILED_PLACEHOLDER
        assert decisions[0]["reasoning"] == DECRYPT_FAILED_PLACEHOLDER
        assert "enc:v2c:" not in json.dumps(decisions[0], ensure_ascii=False)

    def test_playbook_wrong_key_returns_placeholder(self, tmp_path, monkeypatch):
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        added = e.add_playbook({
            "title": "release flow",
            "description": "pypi + github",
            "steps": [{"order": 1, "action": "run tests"}],
        })
        pb_id = added["id"]

        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        pb = e2.get_playbook(pb_id)
        assert pb["title"] == DECRYPT_FAILED_PLACEHOLDER
        assert pb["description"] == DECRYPT_FAILED_PLACEHOLDER
        assert "enc:v2c:" not in json.dumps(pb, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CRITICAL data-safety: a wrong-key read must NOT destroy recoverable ciphertext
# ---------------------------------------------------------------------------


class TestNoDataLossOnWrongKeyRead:
    def test_disk_ciphertext_preserved_after_wrong_key_read(
        self, tmp_path, monkeypatch
    ):
        """The whole reason the fix is display-only, not a decrypt-layer change.

        A wrong-key read triggers the access-count write-back. That must keep
        the original ciphertext on disk (idempotent re-encrypt of the still
        enc:-prefixed value), so a later correct-key read fully recovers the
        plaintext. If the placeholder had leaked into the write path, this would
        fail (placeholder is one-way).
        """
        # 1) Store with the right key.
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "recoverable secret", "detail": "intact detail"})

        disk_before = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        ct_summary_before = disk_before[0]["summary"]
        assert ct_summary_before.startswith("enc:v2c:")

        # 2) Wrong-key read (with the default _update_access=True access bump).
        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        _ = e2.get_lessons()  # triggers write-back of access_count

        # 3) On-disk content field is STILL ciphertext (placeholder never persisted).
        disk_after = json.loads(
            (engram / "knowledge" / "lessons.json").read_text(encoding="utf-8")
        )
        assert disk_after[0]["summary"].startswith("enc:v2c:")
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        assert disk_after[0]["summary"] != DECRYPT_FAILED_PLACEHOLDER

        # 4) Correct-key read fully recovers the original plaintext.
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        e3 = _make_engram(engram)
        lessons = e3.get_lessons()
        assert lessons[0]["summary"] == "recoverable secret"
        assert lessons[0]["detail"] == "intact detail"

    def test_export_path_keeps_ciphertext_not_placeholder(
        self, tmp_path, monkeypatch
    ):
        """Export/backup reads (_update_access=False) must preserve ciphertext,
        so a wrong-key backup can still be restored later with the right key."""
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "backup me", "detail": "backup detail"})

        monkeypatch.setenv("ENGRAM_SECRET", "wrong-key")
        e2 = _make_engram(engram)
        # Internal/export-style read keeps ciphertext (no placeholder).
        raw = e2.get_lessons(_update_access=False)
        assert raw[0]["summary"].startswith("enc:v2c:")
        assert raw[0]["summary"] != DECRYPT_FAILED_PLACEHOLDER


# ---------------------------------------------------------------------------
# Correct-key reads are unaffected (no regression)
# ---------------------------------------------------------------------------


class TestCorrectKeyUnaffected:
    def test_correct_key_read_returns_plaintext(self, tmp_path, monkeypatch):
        from piia_engram.crypto import DECRYPT_FAILED_PLACEHOLDER
        monkeypatch.setenv("ENGRAM_SECRET", "right-key")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "正常可解密", "detail": "fine"})

        lessons = e.get_lessons()
        assert lessons[0]["summary"] == "正常可解密"
        assert lessons[0]["summary"] != DECRYPT_FAILED_PLACEHOLDER

    def test_no_secret_plaintext_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_SECRET", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "no encryption here", "detail": "plain"})

        lessons = e.get_lessons()
        assert lessons[0]["summary"] == "no encryption here"
