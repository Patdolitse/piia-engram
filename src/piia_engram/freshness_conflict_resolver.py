"""Freshness/conflict resolver proposals.

This module builds a unified, metadata-only proposal from already-loaded
lessons and decisions. It may inspect stored bodies to classify stale/conflicting
knowledge, but it never returns those bodies, never reads the store, and never
applies a change. Apply paths remain separate and owner-gated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import lifecycle as _lifecycle
from . import provenance as _provenance
from . import reconcile_proposal as _rp

ACTION_REFRESH = "refresh_review"
ACTION_CONFLICT = "conflict_review"
ACTION_ARCHIVE = "archive_candidate"

_NEGATION_MARKERS = ("avoid", "never", "do not", "don't", "不要", "避免", "不能")
_AFFIRMATION_MARKERS = ("use", "prefer", "always", "should", "使用", "优先", "应该")
_LESSON_CONFLICT_THRESHOLD = 0.25


def _is_active(entry: dict[str, Any]) -> bool:
    return entry.get("status", "active") == "active"


def _entry_id(entry: dict[str, Any]) -> str:
    value = entry.get("id")
    return value if isinstance(value, str) else ""


def _decision_conflicts(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for i, first in enumerate(decisions):
        for second in decisions[i + 1:]:
            q_score = _rp.similarity(first.get("question"), second.get("question"))
            if q_score < _rp.CONFLICT_QUESTION_THRESHOLD:
                continue
            c_score = _rp.similarity(first.get("choice"), second.get("choice"))
            if c_score >= _rp.CHOICE_DIVERGENCE_THRESHOLD:
                continue
            conflicts.append(
                {
                    "action": ACTION_CONFLICT,
                    "entry_type": "decision",
                    "ids": [_entry_id(first), _entry_id(second)],
                    "reason": "same_question_different_choice",
                    "score": round(q_score, 4),
                }
            )
    return conflicts


def _lesson_conflicts(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for i, first in enumerate(lessons):
        for second in lessons[i + 1:]:
            s1 = str(first.get("summary") or "")
            s2 = str(second.get("summary") or "")
            score = _rp.similarity(s1, s2)
            if score < _LESSON_CONFLICT_THRESHOLD:
                continue
            first_neg = any(marker in s1.lower() for marker in _NEGATION_MARKERS)
            second_neg = any(marker in s2.lower() for marker in _NEGATION_MARKERS)
            first_pos = any(marker in s1.lower() for marker in _AFFIRMATION_MARKERS)
            second_pos = any(marker in s2.lower() for marker in _AFFIRMATION_MARKERS)
            if not ((first_neg and second_pos) or (second_neg and first_pos)):
                continue
            conflicts.append(
                {
                    "action": ACTION_CONFLICT,
                    "entry_type": "lesson",
                    "ids": [_entry_id(first), _entry_id(second)],
                    "reason": "contradictory_advice_same_topic",
                    "score": round(score, 4),
                }
            )
    return conflicts


def _freshness_items(entries: list[dict[str, Any]], now: datetime | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in entries:
        fresh = _provenance.compute_freshness(entry, now=now)
        if fresh.get("skip_decay") is True:
            continue
        status = fresh.get("freshness_status")
        if status not in {"aging", "stale"}:
            continue
        items.append(
            {
                "action": ACTION_REFRESH,
                "entry_type": (
                    "decision" if ("choice" in entry or "question" in entry) else "lesson"
                ),
                "id": _entry_id(entry),
                "reason": f"freshness_{status}",
                "freshness_status": status,
                "age_days": fresh.get("age_days"),
                "basis": fresh.get("basis"),
            }
        )
    return items


def _archive_items(entries: list[dict[str, Any]], now: datetime | None) -> list[dict[str, Any]]:
    report = _lifecycle.build_lifecycle_proposal(entries, now=now)
    items: list[dict[str, Any]] = []
    for proposal in report.get("proposals", []):
        if proposal.get("proposal") not in {
            _lifecycle.PROPOSAL_ARCHIVE,
            _lifecycle.PROPOSAL_PRUNE,
        }:
            continue
        items.append(
            {
                "action": ACTION_ARCHIVE,
                "entry_type": proposal.get("entry_type", ""),
                "id": proposal.get("id", ""),
                "reason": proposal.get("proposal", ""),
                "decay_score": proposal.get("decay_score", 0.0),
                "tier": proposal.get("tier", ""),
            }
        )
    return items


def build_freshness_conflict_proposal(
    lessons: list[dict[str, Any]] | None,
    decisions: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a freshness/conflict proposal; never apply changes.

    Returns metadata only: ids, entry types, action names, reason codes, scores,
    and counts. Stored bodies such as summaries, choices, and reasoning are not
    returned.
    """
    active_lessons = [item for item in (lessons or []) if isinstance(item, dict) and _is_active(item)]
    active_decisions = [
        item for item in (decisions or []) if isinstance(item, dict) and _is_active(item)
    ]
    active_entries = active_lessons + active_decisions

    items: list[dict[str, Any]] = []
    items.extend(_freshness_items(active_entries, now))
    items.extend(_archive_items(active_entries, now))
    items.extend(_decision_conflicts(active_decisions))
    items.extend(_lesson_conflicts(active_lessons))

    counts = {ACTION_REFRESH: 0, ACTION_ARCHIVE: 0, ACTION_CONFLICT: 0}
    for item in items:
        action = item.get("action")
        if action in counts:
            counts[action] += 1

    receipt = {
        "scanned_lessons": len(lessons or []),
        "scanned_decisions": len(decisions or []),
        "active_items": len(active_entries),
        "proposed": len(items),
        "applied": False,
    }
    return {
        "counts": counts,
        "items": items,
        "receipt": receipt,
        "invariant": "proposal_only_metadata",
    }
