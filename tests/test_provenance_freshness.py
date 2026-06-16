"""Tests for the Provenance & Freshness Contract v1 helpers.

These cover the pure helper module (``piia_engram.provenance``). They assert
both the contract behavior and the backward-compatibility guarantees: entries
without the new fields must annotate cleanly, and the helpers must never mutate
their inputs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

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


# --- classify_freshness_source --------------------------------------------


def test_classify_confirmation_source_normalizes_case_and_whitespace():
    entry = {"provenance": {"confirmation_source": " Test_Signal "}}
    assert P.classify_freshness_source(entry) == P.SOURCE_SIGNAL


def test_classify_invalid_confirmation_source_falls_back_to_agent():
    entry = {
        "source_tool": "codex",
        "provenance": {"confirmation_source": "not-a-source"},
    }
    assert P.classify_freshness_source(entry) == P.SOURCE_AGENT


@pytest.mark.parametrize(
    "entry",
    [
        {
            "tier": "staging",
            "status": "active",
            "provenance": {"source_agent": "owner"},
        },
        {
            "tier": "verified",
            "status": "inactive",
            "provenance": {"source_agent": "owner"},
        },
        {
            "tier": "verified",
            "provenance": {"source_agent": "owner"},
        },
    ],
)
def test_legacy_human_source_requires_explicit_verified_active(entry):
    assert P.classify_freshness_source(entry) == P.SOURCE_AGENT


def test_source_tool_owner_counts_as_human_only_when_verified_active():
    assert (
        P.classify_freshness_source(
            {"tier": "verified", "status": "active", "source_tool": "owner"}
        )
        == P.SOURCE_HUMAN
    )
    assert (
        P.classify_freshness_source({"tier": "verified", "source_tool": "owner"})
        == P.SOURCE_AGENT
    )


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
    assert ann["basis"] == P.BASIS_NONE
    assert ann["temporal_status"] == P.UNKNOWN
    assert ann["clock_skewed"] is False


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


def test_freshness_future_timestamp_is_clock_skewed_unknown():
    ann = P.compute_freshness({"last_reviewed": _days_ago(-10)}, now=NOW)
    assert ann["freshness_status"] == P.UNKNOWN
    assert ann["temporal_status"] == P.UNKNOWN
    assert ann["clock_skewed"] is True
    assert ann["skew_days"] == 10.0
    assert ann["reason"] == "timestamp_in_future"


@pytest.mark.parametrize(
    "delta",
    [
        timedelta(seconds=1),
        timedelta(seconds=119, microseconds=999999),
        timedelta(minutes=2),
    ],
)
def test_future_timestamp_within_tolerance_is_fresh(delta):
    ann = P.compute_freshness({"last_reviewed": (NOW + delta).isoformat()}, now=NOW)
    assert ann["freshness_status"] == P.FRESH
    assert ann["temporal_status"] == P.FRESH
    assert ann["clock_skewed"] is False
    assert ann["age_days"] == 0.0


def test_future_timestamp_beyond_tolerance_is_clock_skewed_unknown():
    ts = NOW + timedelta(minutes=2, microseconds=1)
    ann = P.compute_freshness({"last_reviewed": ts.isoformat()}, now=NOW)
    assert ann["freshness_status"] == P.UNKNOWN
    assert ann["temporal_status"] == P.UNKNOWN
    assert ann["clock_skewed"] is True
    assert ann["reason"] == "timestamp_in_future"


def test_legacy_entry_without_new_fields_keeps_four_state_status():
    ann = P.compute_freshness({"created_at": _days_ago(5)}, now=NOW)
    assert {"freshness_status", "age_days", "basis", "as_of"} <= set(ann)
    assert ann["freshness_status"] in {P.FRESH, P.AGING, P.STALE, P.UNKNOWN}
    assert ann["temporal_status"] in {P.FRESH, P.AGING, P.STALE, P.UNKNOWN}
    assert ann["source_class"] == P.SOURCE_UNKNOWN
    assert ann["decay_policy"] == "time"
    assert ann["skip_decay"] is False
    assert ann["clock_skewed"] is False


def test_owner_validated_legacy_human_source_still_decays_by_time():
    entry = {
        "tier": "verified",
        "status": "active",
        "created_at": _days_ago(91),
        "provenance": {"source_agent": "owner"},
    }
    ann = P.compute_freshness(entry, now=NOW)
    assert P.classify_freshness_source(entry) == P.SOURCE_HUMAN
    assert ann["source_class"] == P.SOURCE_HUMAN
    assert ann["decay_policy"] == "time"
    assert ann["skip_decay"] is False
    assert ann["temporal_status"] == P.STALE
    assert ann["freshness_status"] == P.STALE


def test_test_signal_uses_trigger_policy_but_reports_temporal_stale():
    ann = P.compute_freshness(
        {
            "created_at": _days_ago(200),
            "provenance": {"confirmation_source": "test_signal"},
        },
        now=NOW,
    )
    assert ann["source_class"] == P.SOURCE_SIGNAL
    assert ann["decay_policy"] == "trigger"
    assert ann["skip_decay"] is True
    assert ann["temporal_status"] == P.STALE
    assert ann["freshness_status"] == P.STALE


def test_anchor_requires_valid_anchor_status_to_skip_decay():
    unvalidated = P.compute_freshness(
        {
            "created_at": _days_ago(200),
            "provenance": {"confirmation_source": "anchor"},
        },
        now=NOW,
    )
    valid = P.compute_freshness(
        {
            "created_at": _days_ago(200),
            "provenance": {
                "confirmation_source": "anchor",
                "anchor_status": "valid",
            },
        },
        now=NOW,
    )

    assert unvalidated["source_class"] == P.SOURCE_ANCHOR
    assert unvalidated["decay_policy"] == "time"
    assert unvalidated["skip_decay"] is False
    assert unvalidated["freshness_status"] == P.STALE

    assert valid["source_class"] == P.SOURCE_ANCHOR
    assert valid["decay_policy"] == "trigger"
    assert valid["skip_decay"] is True
    assert valid["temporal_status"] == P.STALE
    assert valid["freshness_status"] == P.STALE


@pytest.mark.parametrize("anchor_status", [" VALID ", "Valid", "valid"])
def test_anchor_status_is_normalized(anchor_status):
    ann = P.compute_freshness(
        {
            "created_at": _days_ago(200),
            "provenance": {
                "confirmation_source": "anchor",
                "anchor_status": anchor_status,
            },
        },
        now=NOW,
    )
    assert ann["source_class"] == P.SOURCE_ANCHOR
    assert ann["decay_policy"] == P.DECAY_POLICY_TRIGGER
    assert ann["skip_decay"] is True


@pytest.mark.parametrize("anchor_status", [None, "", "invalid", " expired "])
def test_anchor_missing_or_invalid_status_decays_by_time(anchor_status):
    provenance = {"confirmation_source": "anchor"}
    if anchor_status is not None:
        provenance["anchor_status"] = anchor_status
    ann = P.compute_freshness(
        {"created_at": _days_ago(200), "provenance": provenance},
        now=NOW,
    )
    assert ann["source_class"] == P.SOURCE_ANCHOR
    assert ann["decay_policy"] == P.DECAY_POLICY_TIME
    assert ann["skip_decay"] is False


def test_resolve_policy_valid_custom_shapes():
    assert P._resolve_policy(
        P.SOURCE_AGENT,
        {P.SOURCE_AGENT: {"fresh_days": 7, "aging_days": 14, "policy": "trigger"}},
    ) == (7.0, 14.0, P.DECAY_POLICY_TRIGGER)
    assert P._resolve_policy(P.SOURCE_AGENT, {P.SOURCE_AGENT: (5, 9)}) == (
        5.0,
        9.0,
        P.DECAY_POLICY_TIME,
    )
    assert P._resolve_policy(P.SOURCE_SIGNAL, {"default": ((2, 3), "time")}) == (
        2.0,
        3.0,
        P.DECAY_POLICY_TIME,
    )


@pytest.mark.parametrize(
    "policy",
    [
        {"fresh_days": -1, "aging_days": 10, "decay_policy": "trigger"},
        {"fresh_days": 10, "aging_days": 5, "decay_policy": "trigger"},
        {"fresh_days": math.nan, "aging_days": 5, "decay_policy": "trigger"},
        {"fresh_days": 1, "aging_days": math.inf, "decay_policy": "trigger"},
    ],
)
def test_resolve_policy_invalid_thresholds_fall_back_to_entire_default(policy):
    assert P._resolve_policy(P.SOURCE_AGENT, {P.SOURCE_AGENT: policy}) == (
        P.FRESH_MAX_DAYS,
        P.AGING_MAX_DAYS,
        P.DECAY_POLICY_TIME,
    )


def test_resolve_policy_invalid_decay_policy_keeps_valid_thresholds():
    assert P._resolve_policy(
        P.SOURCE_SIGNAL,
        {P.SOURCE_SIGNAL: (3, 4, "bogus")},
    ) == (3.0, 4.0, P.DECAY_POLICY_TRIGGER)


# --- annotate_freshness (non-destructive) ----------------------------------

def test_annotate_is_non_destructive():
    original = {"summary": "x", "last_reviewed": _days_ago(5)}
    snapshot = dict(original)
    out = P.annotate_freshness([original], now=NOW)
    assert "freshness" in out[0]
    # input dict untouched
    assert original == snapshot
    assert "freshness" not in original


def test_annotate_does_not_share_nested_provenance():
    original = {
        "summary": "x",
        "last_reviewed": _days_ago(5),
        "provenance": {"source_agent": "codex"},
    }
    [annotated] = P.annotate_freshness([original], now=NOW)
    annotated["provenance"]["source_agent"] = "mutated"
    assert original["provenance"]["source_agent"] == "codex"


def test_annotate_returns_fully_isolated_deep_copy():
    original = {
        "summary": "x",
        "last_reviewed": _days_ago(5),
        "steps": [{"action": "keep"}],
        "tags": ["stable"],
        "metadata": {"nested": {"value": 1}},
    }
    [annotated] = P.annotate_freshness([original], now=NOW)

    annotated["steps"][0]["action"] = "mutated"
    annotated["tags"].append("mutated")
    annotated["metadata"]["nested"]["value"] = 2

    assert original["steps"] == [{"action": "keep"}]
    assert original["tags"] == ["stable"]
    assert original["metadata"] == {"nested": {"value": 1}}


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
    assert annotated["freshness"]["freshness_status"] in {
        P.FRESH, P.AGING, P.STALE, P.UNKNOWN,
    }
    assert P.resolve_source_agent(legacy) == "codex"
    assert legacy == snapshot  # unchanged


def test_clean_identifier_accepts_namespaced_identifiers():
    assert P._clean_identifier("github:actions") == "github:actions"
    assert P._clean_identifier("org/tool") == "org/tool"
    assert P._clean_identifier("org/tool:v1") == "org/tool:v1"
    assert P._clean_identifier("codex") == "codex"


@pytest.mark.parametrize(
    "value",
    [
        "agent\\name",
        "/agent",
        "agent/",
        ":agent",
        "agent:",
        "C:",
        "C:/Users/pp3x3",
        "C:\\Users\\pp3x3",
        "..",
        "../secrets",
        "a/../b",
        "~",
        "~/secrets",
        "has\nnewline",
    ],
)
def test_clean_identifier_rejects_filesystem_path_shapes(value):
    assert P._clean_identifier(value) is None


def test_clean_identifier_rejects_overlong_and_non_strings():
    assert P._clean_identifier("x" * 121) is None
    assert P._clean_identifier(None) is None
    assert P._clean_identifier("agent\\name") is None
    assert P._clean_identifier("agent:name") is None
    assert P._clean_identifier("C:\\Users\\pp3x3") is None
    assert P._clean_identifier("..\\secrets") is None
    assert P._clean_identifier("../secrets") is None
    assert P._clean_identifier("codex") == "codex"
