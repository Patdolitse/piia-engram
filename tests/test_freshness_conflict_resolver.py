"""Freshness/conflict resolver proposal tests.

The resolver is proposal-only: it may inspect bodies to classify risk, but its
return value must stay metadata-only and must never mutate caller data.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from piia_engram import freshness_conflict_resolver as fcr


NOW = datetime(2026, 6, 6, tzinfo=timezone.utc)


def test_stale_items_are_proposed_for_review_without_body_leakage():
    lessons = [
        {
            "id": "L-stale",
            "summary": "SECRET lesson body about release process",
            "timestamp": "2025-01-01T00:00:00+00:00",
            "status": "active",
        }
    ]

    proposal = fcr.build_freshness_conflict_proposal(lessons, [], now=NOW)

    assert proposal["counts"]["refresh_review"] == 1
    item = proposal["items"][0]
    assert item["id"] == "L-stale"
    assert item["action"] == "refresh_review"
    assert item["freshness_status"] == "stale"
    assert "SECRET lesson body" not in repr(proposal)
    assert proposal["receipt"]["applied"] is False


def test_decision_conflict_is_metadata_only():
    decisions = [
        {
            "id": "D-old",
            "question": "Which release mechanism should Engram use?",
            "choice": "Manual tag first",
            "status": "active",
        },
        {
            "id": "D-new",
            "question": "Which release mechanism should Engram use?",
            "choice": "CI orchestrator first",
            "status": "active",
        },
    ]

    proposal = fcr.build_freshness_conflict_proposal([], decisions, now=NOW)

    conflicts = [item for item in proposal["items"] if item["action"] == "conflict_review"]
    assert conflicts == [
        {
            "action": "conflict_review",
            "entry_type": "decision",
            "ids": ["D-old", "D-new"],
            "reason": "same_question_different_choice",
            "score": 1.0,
        }
    ]
    assert "Manual tag first" not in repr(proposal)
    assert "CI orchestrator first" not in repr(proposal)


def test_resolver_never_mutates_inputs():
    lessons = [
        {
            "id": "L1",
            "summary": "Use pytest for this workflow",
            "timestamp": "2026-06-01T00:00:00+00:00",
            "status": "active",
        }
    ]
    decisions = [
        {
            "id": "D1",
            "question": "Runtime?",
            "choice": "Python",
            "status": "active",
        }
    ]
    before = (deepcopy(lessons), deepcopy(decisions))

    proposal = fcr.build_freshness_conflict_proposal(lessons, decisions, now=NOW)

    assert (lessons, decisions) == before
    assert proposal["receipt"]["applied"] is False
    assert proposal["invariant"] == "proposal_only_metadata"
