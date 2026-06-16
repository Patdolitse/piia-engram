"""Tests for memory lifecycle / decay scoring (Phase 7).

Covers scoring monotonicity, the never-auto-delete invariant, proposal mapping,
metadata-only output, and a synthetic-scale proof that the proposal path handles
many entries without acting on any of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from piia_engram import lifecycle


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_fresh_used_verified_scores_low():
    entry = {
        "id": "a", "summary": "a solid, durable lesson about deployment",
        "last_validated_at": _iso(2), "access_count": 20, "tier": "verified",
    }
    scored = lifecycle.score_entry(entry, now=NOW)
    assert scored["decay_score"] < lifecycle.ARCHIVE_THRESHOLD


def test_stale_unused_staging_scores_high():
    entry = {
        "id": "b", "summary": "an old staging note nobody used",
        "created_at": _iso(400), "access_count": 0, "tier": "staging",
    }
    scored = lifecycle.score_entry(entry, now=NOW)
    assert scored["decay_score"] >= lifecycle.PRUNE_THRESHOLD
    assert "never_accessed" in scored["reasons"]
    assert "staging" in scored["reasons"]
    assert "freshness_stale" in scored["reasons"]


def test_skip_decay_test_signal_does_not_add_freshness_decay():
    entry = {
        "id": "signal",
        "summary": "old but test-backed lesson text",
        "created_at": _iso(500),
        "last_reviewed": _iso(500),
        "access_count": 20,
        "tier": "verified",
        "provenance": {"confirmation_source": "test_signal"},
    }
    scored = lifecycle.score_entry(entry, now=NOW)
    assert scored["freshness_status"] == "stale"
    assert scored["decay_score"] < lifecycle.ARCHIVE_THRESHOLD
    assert "freshness_stale" not in scored["reasons"]


def test_skip_decay_valid_anchor_does_not_add_freshness_decay():
    entry = {
        "id": "anchor",
        "summary": "old but anchor-backed lesson text",
        "created_at": _iso(500),
        "last_reviewed": _iso(500),
        "access_count": 20,
        "tier": "verified",
        "provenance": {
            "confirmation_source": "anchor",
            "anchor_status": " VALID ",
        },
    }
    scored = lifecycle.score_entry(entry, now=NOW)
    assert scored["freshness_status"] == "stale"
    assert scored["decay_score"] < lifecycle.ARCHIVE_THRESHOLD
    assert "freshness_stale" not in scored["reasons"]


def test_score_is_monotonic_in_age():
    young = {"id": "y", "summary": "lesson text long enough to pass", "created_at": _iso(10), "access_count": 0}
    old = {"id": "o", "summary": "lesson text long enough to pass", "created_at": _iso(300), "access_count": 0}
    sy = lifecycle.score_entry(young, now=NOW)["decay_score"]
    so = lifecycle.score_entry(old, now=NOW)["decay_score"]
    assert so > sy


def test_unknown_metadata_never_prunes():
    # No dates, no tier, no access — must not be a prune candidate.
    entry = {"id": "u", "summary": "a lesson with sparse metadata but real text"}
    report = lifecycle.build_lifecycle_proposal([entry], now=NOW)
    proposal = report["proposals"][0]["proposal"]
    assert proposal != lifecycle.PROPOSAL_PRUNE


def test_verified_high_decay_goes_to_review_not_prune():
    # Even a very stale verified+accessed entry is reviewed, never proposed for prune.
    entry = {
        "id": "v", "summary": "important but old verified lesson",
        "created_at": _iso(800), "access_count": 0, "tier": "verified",
    }
    report = lifecycle.build_lifecycle_proposal([entry], now=NOW)
    proposal = report["proposals"][0]["proposal"]
    assert proposal in (lifecycle.PROPOSAL_REVIEW, lifecycle.PROPOSAL_ARCHIVE)
    assert proposal != lifecycle.PROPOSAL_PRUNE


def test_proposal_is_metadata_only():
    entry = {
        "id": "secretid", "summary": "SENSITIVE-BODY-TEXT", "detail": "MORE-SECRET",
        "created_at": _iso(400), "access_count": 0, "tier": "staging",
    }
    report = lifecycle.build_lifecycle_proposal([entry], now=NOW)
    blob = repr(report)
    assert "SENSITIVE-BODY-TEXT" not in blob
    assert "MORE-SECRET" not in blob
    # id IS metadata and may appear; the body must not.
    assert "secretid" in blob


def test_never_auto_delete_invariant_present():
    report = lifecycle.build_lifecycle_proposal([{"id": "x", "summary": "y" * 30}], now=NOW)
    assert report["invariant"] == "never_auto_delete"


def test_malformed_entries_counted_but_not_scored():
    report = lifecycle.build_lifecycle_proposal([None, "str", {"id": "ok", "summary": "z" * 30}], now=NOW)
    assert report["total"] == 3
    assert report["scored"] == 1


def test_synthetic_scale_proposal_path():
    """Proposal path handles many entries; nothing is mutated/deleted."""
    n = 5000
    entries = []
    for i in range(n):
        # Mix of fresh/used and stale/unused so all buckets are exercised.
        if i % 3 == 0:
            entries.append({"id": f"e{i}", "summary": "fresh used lesson body text",
                            "last_validated_at": _iso(3), "access_count": 30, "tier": "verified"})
        elif i % 3 == 1:
            entries.append({"id": f"e{i}", "summary": "stale staging unused note text",
                            "created_at": _iso(500), "access_count": 0, "tier": "staging"})
        else:
            entries.append({"id": f"e{i}", "summary": "aging mid lesson body text",
                            "created_at": _iso(60), "access_count": 2, "tier": "verified"})

    # Snapshot inputs to prove non-destructiveness.
    before = [dict(e) for e in entries]
    report = lifecycle.build_lifecycle_proposal(entries, now=NOW)

    assert report["total"] == n
    assert report["scored"] == n
    assert sum(report["counts"].values()) == n
    # Every bucket got some entries given the mix.
    assert report["counts"][lifecycle.PROPOSAL_PRUNE] > 0
    assert report["counts"][lifecycle.PROPOSAL_KEEP] > 0
    # Sorted most-decayed first.
    scores = [p["decay_score"] for p in report["proposals"]]
    assert scores == sorted(scores, reverse=True)
    # Inputs untouched (never auto-delete / never mutate).
    assert entries == before


def test_render_text_smoke():
    report = lifecycle.build_lifecycle_proposal([
        {"id": "p1", "summary": "stale unused staging text here",
         "created_at": _iso(500), "access_count": 0, "tier": "staging"},
    ], now=NOW)
    text = lifecycle.render_lifecycle_text(report)
    assert "never_auto_delete" in text
    assert "prune" in text
