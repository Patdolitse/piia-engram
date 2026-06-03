"""Recall provenance/freshness default-visibility guard (Node N1).

Proves that provenance and freshness are visible by DEFAULT — in both the
payload and the rendered text digest — and that turning freshness on/off does
not change ranking or item order (visibility only, no retrieval change).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from piia_engram.recall import build_recall_payload
from piia_engram.recall_service import render_recall_text

NOW = datetime(2026, 6, 3)


def _lesson(i: int, *, age_days: int, source: str) -> dict:
    validated = (NOW - timedelta(days=age_days)).isoformat()
    return {
        "id": f"L{i}",
        "type": "lesson",
        "summary": f"lesson {i}",
        "provenance": {
            "source_agent": source,
            "run_id": f"run-{i}",
            "last_validated_at": validated,
        },
        "last_validated_at": validated,
    }


def _payload(*, include_freshness: bool = True):
    entries = [
        _lesson(1, age_days=1, source="claude_code"),
        _lesson(2, age_days=200, source="codex"),
        _lesson(3, age_days=60, source="cursor"),
    ]
    return build_recall_payload(
        relevant_knowledge=entries,
        include_freshness=include_freshness,
        now=NOW,
    )


def test_provenance_and_freshness_present_by_default():
    payload = _payload()  # defaults: include_freshness=True
    items = payload["knowledge"]
    assert len(items) == 3
    for item in items:
        assert "provenance" in item and item["provenance"].get("source_agent")
        assert "freshness" in item and item["freshness"].get("freshness_status")
    # Freshness buckets reflect age relative to NOW.
    by_id = {it["summary"]: it["freshness"]["freshness_status"] for it in items}
    assert by_id["lesson 1"] == "fresh"
    assert by_id["lesson 2"] == "stale"
    assert by_id["lesson 3"] == "aging"


def test_render_shows_provenance_and_freshness_by_default():
    text = render_recall_text(_payload())
    assert "«src:claude_code»" in text
    assert "[fresh]" in text
    assert "[stale]" in text


def test_ranking_order_unchanged_with_or_without_freshness():
    order_with = [it["summary"] for it in _payload(include_freshness=True)["knowledge"]]
    order_without = [it["summary"] for it in _payload(include_freshness=False)["knowledge"]]
    # Order is the input (relevant-first) order, and freshness does not reorder it.
    assert order_with == ["lesson 1", "lesson 2", "lesson 3"]
    assert order_with == order_without


def test_freshness_off_hides_freshness_only_keeps_provenance():
    items = _payload(include_freshness=False)["knowledge"]
    for item in items:
        assert "freshness" not in item
        assert item["provenance"].get("source_agent")  # provenance still present
