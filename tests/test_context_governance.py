"""Tests for the unified context-governance proposal facade."""

from __future__ import annotations

from datetime import datetime, timezone

from piia_engram import context_governance as cg


class FakeEngram:
    root = None

    def get_lessons(self, limit=None, _update_access=True):
        assert _update_access is False
        return [
            {
                "id": "lesson-1",
                "summary": "Prefer Python",
                "status": "active",
                "last_validated_at": "2020-01-01",
            }
        ]

    def get_decisions(self, limit=None, _update_access=True):
        assert _update_access is False
        return [
            {
                "id": "decision-1",
                "question": "Use API?",
                "choice": "Yes",
                "status": "active",
                "last_validated_at": "2020-01-01",
            }
        ]

    def get_safe_profile(self):
        return {"role": "tester", "language": "zh"}

    def get_recent_context(self, limit=1):
        return []

    def get_relevant_lessons(self, **kwargs):
        return [{"id": "recall-1", "summary": "token sk-test_1234567890abcdef1234567890abcdef"}]


def _now():
    return datetime(2026, 6, 6, tzinfo=timezone.utc)


def test_safe_context_mode_redacts_and_marks_preview_only():
    result = cg.build_context_governance_preview(
        "safe_context",
        payload={"knowledge": [{"summary": "token sk-test_1234567890abcdef1234567890abcdef"}]},
        options={"max_chars": 2000},
        now=_now(),
    )

    assert result["mode"] == "safe_context"
    assert result["applied"] is False
    assert result["invariant"] == "context_governance_preview_only"
    assert "sk-test_" not in repr(result)
    assert result["proposal"]["meta"]["safe_context"]["mode"] == "safe"


def test_safe_context_mode_can_gather_recall_when_payload_empty():
    result = cg.build_context_governance_preview(
        "safe_context",
        engram=FakeEngram(),
        options={"max_chars": 2000},
        now=_now(),
    )

    assert result["mode"] == "safe_context"
    assert result["proposal"]["identity"]["role"] == "tester"
    assert "sk-test_" not in repr(result)


def test_freshness_conflicts_mode_returns_metadata_only_proposal():
    result = cg.build_context_governance_preview(
        "freshness_conflicts",
        engram=FakeEngram(),
        now=_now(),
    )

    assert result["mode"] == "freshness_conflicts"
    assert result["applied"] is False
    assert result["proposal"]["invariant"] == "proposal_only_metadata"
    assert "Prefer Python" not in repr(result)
    assert "Use API?" not in repr(result)


def test_replay_packet_mode_redacts_and_never_applies():
    result = cg.build_context_governance_preview(
        "replay_packet",
        payload={"compact_summary": "keep this token sk-test_1234567890abcdef1234567890abcdef"},
        options={"source": "test", "max_summary_chars": 80},
        now=_now(),
    )

    packet = result["proposal"]
    assert result["mode"] == "replay_packet"
    assert packet["applied"] is False
    assert packet["invariant"] == "replay_packet_only"
    assert "sk-test_" not in repr(result)


def test_external_evidence_mode_renders_local_markdown_draft():
    result = cg.build_context_governance_preview(
        "external_evidence",
        payload={
            "evidence": [
                {
                    "label": "PyPI",
                    "status": "verified",
                    "checked_at": "2026-06-06",
                    "url": "https://example.test/pkg",
                }
            ]
        },
        options={"title": "Evidence Draft"},
    )

    draft = result["proposal"]["draft"]
    assert result["mode"] == "external_evidence"
    assert result["applied"] is False
    assert "# Evidence Draft" in draft
    assert "LOCAL DRAFT" in draft
    assert "owner confirmation" in draft.lower()


def test_unknown_mode_is_explicit_error():
    result = cg.build_context_governance_preview("nope")

    assert result["error"] == "unknown_mode"
    assert sorted(result["allowed_modes"]) == sorted(cg.MODES)
    assert result["applied"] is False
