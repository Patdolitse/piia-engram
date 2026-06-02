"""Tests for the Provenance & Freshness Contract v1 helpers.

These cover the pure helper module (``piia_engram.provenance``). They assert
both the contract behavior and the backward-compatibility guarantees: entries
without the new fields must annotate cleanly, and the helpers must never mutate
their inputs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from piia_engram import provenance as P


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _days_ago(n: float) -> str:
    return (NOW - timedelta(days=n)).isoformat()


# --- normalize_provenance_fields -------------------------------------------

def test_normalize_keeps_valid_fields():
    out = P.normalize_provenance_fields(
        {
            "source_agent": "claude_code",
            "run_id": "wf_abc123",
            "last_validated_at": "2026-05-01T00:00:00Z",
        }
    )
    assert out["source_agent"] == "claude_code"
    assert out["run_id"] == "wf_abc123"
    # normalized to UTC ISO form
    assert out["last_validated_at"].startswith("2026-05-01T00:00:00")


def test_normalize_drops_malformed_fields():
    out = P.normalize_provenance_fields(
        {
            "source_agent": "has\nnewline",          # content-like → dropped
            "run_id": "x" * 200,                       # too long → dropped
            "last_validated_at": "not-a-date",         # unparseable → dropped
        }
    )
    assert out == {}


def test_normalize_ignores_absent_and_nondict():
    assert P.normalize_provenance_fields({}) == {}
    assert P.normalize_provenance_fields(None) == {}  # type: ignore[arg-type]


# --- resolve_source_agent --------------------------------------------------

def test_resolve_source_agent_prefers_provenance():
    entry = {"source_tool": "codex", "provenance": {"source_agent": "claude_code"}}
    assert P.resolve_source_agent(entry) == "claude_code"


def test_resolve_source_agent_falls_back_to_source_tool():
    entry = {"source_tool": "codex"}
    assert P.resolve_source_agent(entry) == "codex"


def test_resolve_source_agent_empty_when_unknown():
    assert P.resolve_source_agent({}) == ""


# --- compute_freshness -----------------------------------------------------

def test_freshness_fresh():
    entry = {"last_reviewed": _days_ago(5)}
    ann = P.compute_freshness(entry, now=NOW)
    assert ann["freshness_status"] == P.FRESH
    assert ann["basis"] == "last_reviewed"
    assert ann["age_days"] == 5.0


def test_freshness_aging():
    entry = {"created_at": _days_ago(60)}
    ann = P.compute_freshness(entry, now=NOW)
    assert ann["freshness_status"] == P.AGING
    assert ann["basis"] == "created_at"


def test_freshness_stale():
    entry = {"timestamp": _days_ago(200)}
    ann = P.compute_freshness(entry, now=NOW)
    assert ann["freshness_status"] == P.STALE


def test_freshness_unknown_when_no_timestamp():
    ann = P.compute_freshness({"summary": "no dates here"}, now=NOW)
    assert ann["freshness_status"] == P.UNKNOWN
    assert ann["age_days"] is None
    assert ann["basis"] == "none"


def test_freshness_basis_priority_prefers_last_validated_at():
    entry = {
        "provenance": {"last_validated_at": _days_ago(2)},
        "last_reviewed": _days_ago(100),
        "created_at": _days_ago(300),
    }
    ann = P.compute_freshness(entry, now=NOW)
    assert ann["basis"] == "last_validated_at"
    assert ann["freshness_status"] == P.FRESH


def test_freshness_boundary_30_days_is_fresh():
    ann = P.compute_freshness({"last_reviewed": _days_ago(30)}, now=NOW)
    assert ann["freshness_status"] == P.FRESH


def test_freshness_boundary_91_days_is_stale():
    ann = P.compute_freshness({"last_reviewed": _days_ago(91)}, now=NOW)
    assert ann["freshness_status"] == P.STALE


def test_freshness_future_timestamp_clamps_to_zero():
    ann = P.compute_freshness({"last_reviewed": _days_ago(-10)}, now=NOW)
    assert ann["age_days"] == 0.0
    assert ann["freshness_status"] == P.FRESH


# --- annotate_freshness (non-destructive) ----------------------------------

def test_annotate_is_non_destructive():
    original = {"summary": "x", "last_reviewed": _days_ago(5)}
    snapshot = dict(original)
    out = P.annotate_freshness([original], now=NOW)
    assert "freshness" in out[0]
    # input dict untouched
    assert original == snapshot
    assert "freshness" not in original


def test_annotate_passes_through_non_dicts():
    out = P.annotate_freshness(["not a dict", 42], now=NOW)
    assert out == ["not a dict", 42]


def test_annotate_handles_empty_and_none():
    assert P.annotate_freshness([]) == []
    assert P.annotate_freshness(None) == []  # type: ignore[arg-type]


def test_backward_compat_entry_without_any_new_fields():
    """An entry shaped like today's stored lessons annotates without error."""
    legacy = {
        "id": "abc123",
        "summary": "old lesson",
        "source_tool": "codex",
        "created_at": _days_ago(10),
        "tier": "verified",
        "status": "active",
    }
    snapshot = dict(legacy)
    [annotated] = P.annotate_freshness([legacy], now=NOW)
    assert annotated["freshness"]["freshness_status"] == P.FRESH
    assert P.resolve_source_agent(legacy) == "codex"
    assert legacy == snapshot  # unchanged
