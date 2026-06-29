"""Review/staging surfaces for session-derived evidence metadata."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging, list_pending_staging


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _extract_staged_session_lesson(
    eng: Engram,
    *,
    source_ref: str = "session-review-001",
) -> dict:
    summary = (
        "Tests: pytest tests/test_example.py passed.\n"
        "Remember to run twine check before publishing because it catches "
        "package metadata errors."
    )
    result = eng.extract_session_insights(
        summary,
        source_tool="codex",
        source_ref=source_ref,
        force_staging=True,
    )
    assert result["saved_lessons"] == 1
    return next(
        item for item in eng.get_lessons(limit=None, _update_access=False)
        if item.get("tier") == "staging"
    )


def _pending_row(payload: dict, item_id: str) -> dict:
    return next(item for item in payload["items"] if item["id"] == item_id)


def test_session_derived_decision_surfaces_review_evidence_metadata(
    tmp_path: Path,
):
    eng = _eng(tmp_path)
    result = eng.extract_session_insights(
        "Tests: pytest tests/test_example.py passed.\n"
        "We decided to keep owner review mandatory for evidence promotion.",
        source_tool="codex",
        source_ref="decision-review-001",
        force_staging=True,
    )
    assert result["saved_decisions"] == 1
    decision = next(
        item for item in eng.get_decisions(limit=None, _update_access=False)
        if item.get("tier") == "staging"
    )

    payload = list_pending_staging(eng, limit=10)
    row = _pending_row(payload, decision["id"])

    assert row["type"] == "decision"
    assert row["evidence"]["source_type"] == "session_digest"
    assert row["evidence"]["source_ref"] == "decision-review-001"
    assert row["evidence"]["promotion_hint"] == "needs_owner_review"


def test_session_derived_staging_item_surfaces_review_evidence_metadata(
    tmp_path: Path,
):
    eng = _eng(tmp_path)
    lesson = _extract_staged_session_lesson(eng)

    payload = list_pending_staging(eng, limit=10)
    row = _pending_row(payload, lesson["id"])

    assert row["evidence"] == {
        "source_type": "session_digest",
        "source_tool": "codex",
        "source_ref": "session-review-001",
        "verification_status": "passed",
        "confidence": "high",
        "promotion_hint": "needs_owner_review",
    }
    assert row["tier"] == "staging"
    assert row["status"] == "pending"
    assert row["promotion_suggested"] is False


def test_review_evidence_does_not_bypass_owner_review(tmp_path: Path):
    eng = _eng(tmp_path)
    lesson = _extract_staged_session_lesson(eng)

    payload = batch_review_staging(
        eng,
        [{"id": lesson["id"], "action": "approve"}],
        dry_run=False,
        confirm=False,
    )

    assert payload["status"] == "confirmation_required"
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    stored = next(
        item for item in eng.get_lessons(limit=None, _update_access=False)
        if item["id"] == lesson["id"]
    )
    assert stored["tier"] == "staging"
    assert stored["evidence"]["promotion_hint"] == "needs_owner_review"


def test_direct_staging_write_does_not_invent_review_evidence(tmp_path: Path):
    eng = _eng(tmp_path)
    lesson = eng.add_lesson({
        "summary": "Direct staging lesson",
        "tier": "staging",
        "evidence": {
            "source_type": "session_digest",
            "source_tool": "codex",
            "source_ref": "forged-session",
            "verification_status": "passed",
            "confidence": "high",
            "promotion_hint": "needs_owner_review",
        },
        "extraction": {
            "method": "session_insights",
            "source_tool": "codex",
            "quality_score": 1.0,
            "trigger_reason": "lesson_trigger",
        },
    })

    payload = batch_review_staging(eng, [], operation="list_pending", limit=10)
    row = _pending_row(payload, lesson["id"])

    assert "evidence" not in row
    stored = next(
        item for item in eng.get_lessons(limit=None, _update_access=False)
        if item["id"] == lesson["id"]
    )
    assert "evidence" not in stored


def test_source_ref_is_redacted_on_review_surface(tmp_path: Path):
    eng = _eng(tmp_path)
    fake_key = "sk-" + "abcdef1234567890ABCDEF"
    raw_source_ref = f"E:\\Private\\sessions\\{fake_key}"
    lesson = _extract_staged_session_lesson(eng, source_ref=raw_source_ref)

    payload = list_pending_staging(eng, limit=10)
    row = _pending_row(payload, lesson["id"])
    blob = json.dumps(row["evidence"], ensure_ascii=False)

    assert fake_key not in blob
    assert "E:\\Private" not in blob
    assert row["evidence"]["source_ref"]


def test_cli_review_list_and_show_surface_session_evidence_safely(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = _extract_staged_session_lesson(Engram())

    assert run_review([]) == 0
    list_out = capsys.readouterr().out
    assert "evidence=session_digest" in list_out
    assert "codex" not in list_out
    assert "session-review-001" not in list_out
    assert "needs_owner_review" not in list_out

    assert run_review(["show", lesson["id"]]) == 0
    show_out = capsys.readouterr().out
    assert "session_digest" in show_out
    assert "codex" in show_out
    assert "session-review-001" in show_out
    assert "confidence=high" in show_out
    assert "needs_owner_review" in show_out
