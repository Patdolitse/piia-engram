"""Evidence metadata for session-derived memory candidates."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram

_FAKE_SK_KEY = "sk-" + "abcdef1234567890ABCDEF"


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def test_session_derived_lesson_carries_evidence_metadata(tmp_path: Path):
    eng = _eng(tmp_path)
    summary = (
        "Tests: pytest tests/test_example.py passed.\n"
        "Remember to run twine check before publishing because it catches package metadata errors."
    )

    result = eng.extract_session_insights(
        summary,
        source_tool="codex",
        source_ref="session-123",
        force_staging=True,
    )

    assert result["saved_lessons"] == 1
    lesson = eng.get_lessons(limit=None, _update_access=False)[0]
    assert lesson["tier"] == "staging"
    assert lesson["evidence"] == {
        "source_type": "session_digest",
        "source_tool": "codex",
        "source_ref": "session-123",
        "verification_status": "passed",
        "confidence": "high",
        "promotion_hint": "needs_owner_review",
    }


def test_session_derived_decision_evidence_does_not_verify_candidate(tmp_path: Path):
    eng = _eng(tmp_path)
    summary = "We decided to add twine check to the release gate."

    eng.extract_session_insights(
        summary,
        source_tool="claude_code",
        source_ref="decision-session",
        force_staging=True,
    )

    decision = eng.get_decisions(limit=None, _update_access=False)[0]
    assert decision["tier"] == "staging"
    assert decision["evidence"]["source_type"] == "session_digest"
    assert decision["evidence"]["promotion_hint"] == "needs_owner_review"


def test_direct_user_confirmed_write_is_not_given_session_evidence(tmp_path: Path):
    eng = _eng(tmp_path)

    lesson = eng.add_lesson("Direct user-confirmed lesson", tier="verified")

    assert lesson["tier"] == "verified"
    assert "evidence" not in lesson


def test_session_evidence_metadata_redacts_sensitive_source_ref(tmp_path: Path):
    eng = _eng(tmp_path)
    summary = "Remember to run release checks before publishing because they catch packaging failures."

    eng.extract_session_insights(
        summary,
        source_tool="codex",
        source_ref=f"E:\\Private\\session {_FAKE_SK_KEY}",
        force_staging=True,
    )

    lesson = eng.get_lessons(limit=None, _update_access=False)[0]
    blob = json.dumps(lesson["evidence"], ensure_ascii=False)
    assert _FAKE_SK_KEY not in blob
    assert "E:\\Private" not in blob
