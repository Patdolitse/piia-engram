"""Negative-control regression for the continuity harness leak detector (N10).

The existing harness tests assert ``leak_checks.clean is True`` on good input.
That alone does not prove the detector *works* — a detector that always returned
"clean" would pass every one of them. These controls drive the detector directly
with adversarial input to prove each leak class is actually caught, plus a
positive control proving verified content does NOT false-positive.

They are deliberately structural (no model-output expectations), so they stay a
stable guard for Claude/Codex/Cursor handoff coherence.
"""

from __future__ import annotations

from piia_engram.continuity_harness import _leak_checks


def test_staging_content_in_export_is_flagged():
    lessons = [{"summary": "STAGED_LESSON_MARKER", "tier": "staging"}]
    result = _leak_checks("intro STAGED_LESSON_MARKER tail", lessons, [], [])
    assert result["staging_in_export"] is True
    assert result["clean"] is False


def test_over_sensitive_content_in_export_is_flagged():
    lessons = [{"summary": "SENSITIVE_MARKER", "sensitivity": "secret"}]
    result = _leak_checks("intro SENSITIVE_MARKER tail", lessons, [], [],
                          max_sensitivity="work")
    assert result["sensitive_in_export"] is True
    assert result["clean"] is False


def test_fresh_writeback_in_export_is_flagged():
    staged = [{"summary": "FRESH_WB_MARKER", "tier": "staging",
               "approval_status": "pending"}]
    result = _leak_checks("intro FRESH_WB_MARKER tail", [], [], staged)
    assert result["staged_writeback_in_export"] is True
    assert result["clean"] is False


def test_verified_content_is_not_a_false_positive():
    """A verified, in-ceiling entry present in the export is NOT a leak."""
    lessons = [{"summary": "VERIFIED_MARKER", "tier": "verified"}]
    result = _leak_checks("intro VERIFIED_MARKER tail", lessons, [], [],
                          max_sensitivity="work")
    assert result["clean"] is True
