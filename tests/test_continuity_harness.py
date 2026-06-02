"""Tests for the cross-tool continuity verification harness (Phase 10).

Simulated E2E only — no live tool install is touched, nothing is written.
Covers the writeback governance invariants (drop secret, dedup, staging-only)
and the no-leak guarantee on generated continuity material across Codex/Claude/
Cursor-style export inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import continuity_harness as ch

FIXTURE = Path(__file__).parent / "fixtures" / "continuity_harness_corpus.json"


@pytest.fixture(scope="module")
def corpus():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- writeback preparation invariants (design I2/I3/I6) ----------------------

def test_secret_candidate_dropped_never_staged(corpus):
    out = ch.prepare_writeback_candidates(corpus["writeback"]["codex"])
    assert out["dropped_sensitive"] >= 1
    for item in out["staged"]:
        assert "sk-proj-" not in json.dumps(item)


def test_all_staged_items_are_staging_pending(corpus):
    out = ch.prepare_writeback_candidates(corpus["writeback"]["codex"])
    assert out["staged"], "expected at least one staged item"
    for item in out["staged"]:
        assert item["tier"] == "staging"
        assert item["approval_status"] == "pending"
        # I6: nothing is ever verified by the writeback path.
        assert item["tier"] != "verified"


def test_dedup_by_content_hash(corpus):
    items = corpus["writeback"]["codex"]
    # Pre-seed the hash of the second (duplicate) item.
    h = ch.content_hash("Run pytest from the project virtualenv, not system python", "")
    out = ch.prepare_writeback_candidates(items, existing_hashes={h})
    assert out["skipped_duplicate"] >= 1
    summaries = {s["summary"] for s in out["staged"]}
    assert "Run pytest from the project virtualenv, not system python" not in summaries


def test_private_dropped_by_default_but_staged_on_optin():
    private_item = {"summary": "note", "detail": "contact me at owner@example.com"}
    default = ch.prepare_writeback_candidates([private_item])
    assert default["dropped_sensitive"] == 1
    opted = ch.prepare_writeback_candidates([private_item], allow_private=True)
    assert len(opted["staged"]) == 1
    assert opted["staged"][0]["sensitivity"] == "private"


def test_audit_record_is_metadata_only(corpus):
    out = ch.prepare_writeback_candidates(corpus["writeback"]["codex"], session_id="s1")
    rec = out["audit_record"]
    assert rec["applied"] is False
    assert rec["session_id"] == "s1"
    # Hashes are metadata; no candidate body text in the record.
    blob = json.dumps(rec)
    assert "Keep exports verified-only" not in blob
    assert isinstance(rec["content_hashes"], list)


# --- tool parsers ------------------------------------------------------------

@pytest.mark.parametrize("tool", ["codex", "claude", "cursor"])
def test_parsers_yield_candidates(corpus, tool):
    cands = ch.parse_tool_writeback(tool, corpus["writeback"][tool])
    assert cands, f"{tool} should parse to candidates"
    assert all(isinstance(c, dict) and c.get("summary") for c in cands)


def test_unknown_tool_returns_empty():
    assert ch.parse_tool_writeback("nonsense", "whatever") == []
    assert ch.parse_tool_writeback("codex", "not-a-list") == []


# --- end-to-end simulation across tools --------------------------------------

@pytest.mark.parametrize("tool", ["codex", "claude", "cursor"])
def test_cycle_no_leakage_and_stable_export(corpus, tool):
    store = corpus["store"]
    result = ch.simulate_continuity_cycle(
        lessons=store["lessons"], decisions=store["decisions"],
        tool=tool, tool_writeback=corpus["writeback"][tool],
    )
    # Export is stable (staged writeback never auto-promotes into next export).
    assert result["export_stable"] is True
    # Generated continuity material is clean: no staging, no over-sensitive, no
    # freshly-staged writeback content leaked into the exported block.
    leak = result["leak_checks"]
    assert leak["staging_in_export"] is False
    assert leak["sensitive_in_export"] is False
    assert leak["staged_writeback_in_export"] is False
    assert leak["clean"] is True
    # All writeback items are staging-tier.
    assert result["all_writeback_staged"] is True


def test_staging_and_private_entries_absent_from_export(corpus):
    store = corpus["store"]
    result = ch.simulate_continuity_cycle(
        lessons=store["lessons"], decisions=store["decisions"],
        tool="codex", tool_writeback=corpus["writeback"]["codex"],
    )
    md = result["export_md"]
    assert "UNVERIFIED-STAGING-NOTE" not in md
    assert "owner@example.com" not in md
    # Verified, non-sensitive content IS present.
    assert "Telemetry stays opt-in" in md
    assert "D+ mechanism" in md


def test_empty_store_exports_nothing_but_does_not_crash():
    result = ch.simulate_continuity_cycle(lessons=[], decisions=[], tool="codex",
                                          tool_writeback=[])
    assert "No verified" in result["export_md"]
    assert result["writeback"]["staged"] == []
    assert result["leak_checks"]["clean"] is True
