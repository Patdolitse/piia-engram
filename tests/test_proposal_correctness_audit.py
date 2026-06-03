"""L2: proposal-correctness audit harness.

Beyond "the proposal is well-formed", these tests assert the proposal loops are
*safe and correct on a realistic corpus*: lifecycle never proposes deleting
verified/high-value knowledge, integrity stays read-only/proposal-only, and
reconcile never turns destructive and preserves conflicting source ids. All
executors are proposal-only — there is no apply/delete path to exercise.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from piia_engram import lifecycle, integrity, reconcile_proposal


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Realistic lifecycle corpus
# ---------------------------------------------------------------------------

def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _lifecycle_corpus() -> list[dict]:
    """A spread across age × tier × access × type, including high-value entries
    that must never be proposed for pruning."""
    corpus: list[dict] = []
    for age in (0, 10, 45, 120, 400):
        for tier in ("staging", "verified"):
            for access in (0, 1, 9):
                corpus.append({
                    "id": f"l-{age}-{tier}-{access}",
                    "summary": "metadata-only entry",
                    "domain": "ai",
                    "tier": tier,
                    "access_count": access,
                    "created_at": _iso(age),
                })
    # Explicit high-value: verified, frequently accessed, but ancient.
    corpus.append({
        "id": "high-value-old", "choice": "x", "question": "y",
        "tier": "verified", "access_count": 50, "created_at": _iso(900),
    })
    # A playbook (verified) that is stale and never accessed.
    corpus.append({
        "id": "pb-stale", "steps": ["a", "b"], "triggers": ["t"],
        "tier": "verified", "access_count": 0, "created_at": _iso(500),
    })
    return corpus


def test_lifecycle_never_prunes_verified_or_accessed_knowledge():
    report = lifecycle.build_lifecycle_proposal(_lifecycle_corpus(), now=NOW)
    for p in report["proposals"]:
        if p["proposal"] == lifecycle.PROPOSAL_PRUNE:
            # The ONLY entries ever proposed for pruning are un-promoted (staging)
            # entries that were never accessed.
            assert p["tier"] == "staging", p
            assert p["access_count"] == 0, p
    # And the explicit high-value entry is never a prune candidate.
    hv = next(p for p in report["proposals"] if p["id"] == "high-value-old")
    assert hv["proposal"] != lifecycle.PROPOSAL_PRUNE
    assert hv["proposal"] in (lifecycle.PROPOSAL_KEEP, lifecycle.PROPOSAL_REVIEW,
                              lifecycle.PROPOSAL_ARCHIVE)


def test_lifecycle_high_decay_verified_goes_to_review_not_prune():
    # A verified, never-accessed, very old playbook should surface for *review*,
    # not pruning, because it is promoted knowledge.
    report = lifecycle.build_lifecycle_proposal(_lifecycle_corpus(), now=NOW)
    pb = next(p for p in report["proposals"] if p["id"] == "pb-stale")
    assert pb["proposal"] in (lifecycle.PROPOSAL_REVIEW, lifecycle.PROPOSAL_ARCHIVE)
    assert pb["proposal"] != lifecycle.PROPOSAL_PRUNE


def test_lifecycle_proposal_carries_only_metadata():
    report = lifecycle.build_lifecycle_proposal(_lifecycle_corpus(), now=NOW)
    allowed = {"id", "entry_type", "decay_score", "freshness_status", "age_days",
               "access_count", "tier", "reasons", "proposal"}
    blob = json.dumps(report, ensure_ascii=False)
    assert "metadata-only entry" not in blob  # no summary body leaks
    for p in report["proposals"]:
        assert set(p) <= allowed, set(p) - allowed
    assert report["invariant"] == "never_auto_delete"


# ---------------------------------------------------------------------------
# Integrity: read-only + self-heal/proposal-only on a realistic store
# ---------------------------------------------------------------------------

@pytest.fixture
def realistic_root(tmp_path: Path) -> Path:
    kd = tmp_path / "knowledge"
    kd.mkdir()
    (kd / "lessons.json").write_text(json.dumps([
        {"id": "l1", "summary": "s1", "tier": "verified"},
        {"id": "l2", "summary": "s2", "tier": "staging"},
    ]), encoding="utf-8")
    (kd / "decisions.json").write_text(json.dumps([
        {"id": "d1", "question": "q", "choice": "c", "tier": "verified"},
    ]), encoding="utf-8")
    (kd / "playbooks.json").write_text(json.dumps([]), encoding="utf-8")
    return tmp_path


def _tree_fingerprint(root: Path) -> dict[str, str]:
    fp: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            fp[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return fp


def test_integrity_scan_is_read_only(realistic_root):
    before = _tree_fingerprint(realistic_root)
    report = integrity.scan_integrity(realistic_root, now=NOW)
    after = _tree_fingerprint(realistic_root)
    assert before == after, "integrity scan must not mutate the store"
    assert report["live_store_modified"] is False


def test_integrity_self_heal_proposals_are_all_non_destructive(realistic_root):
    # Introduce a corruption so there is something to propose against.
    (realistic_root / "knowledge" / "lessons.json").write_text(
        '[{"id": "l1"', encoding="utf-8")  # truncated/corrupt JSON
    report = integrity.scan_integrity(realistic_root, now=NOW)
    proposals = integrity.build_self_heal_proposals(report)
    assert proposals, "a corrupt dataset should yield at least one proposal"
    for pr in proposals:
        assert pr["destructive"] is False, pr
        assert "command" in pr and pr["command"]


def test_integrity_report_is_metadata_only(realistic_root):
    report = integrity.scan_integrity(realistic_root, now=NOW)
    blob = json.dumps(report, ensure_ascii=False)
    for body in ("s1", "s2", "\"choice\": \"c\""):
        assert body not in blob, f"integrity report leaked content: {body}"


# ---------------------------------------------------------------------------
# Reconcile: proposal-only, never destructive, conflicting ids preserved
# ---------------------------------------------------------------------------

def _existing_store() -> list[dict]:
    return [
        {"id": "exist-decision-1", "question": "Which database for Engram?",
         "choice": "Use SQLite locally"},
        {"id": "exist-lesson-1", "summary": "Always validate at the send boundary"},
    ]


def test_reconcile_conflict_preserves_conflicting_source_id():
    candidates = [
        # Same question, opposing choice → conflict; must keep the existing id.
        {"id": "cand-1", "question": "Which database for Engram?",
         "choice": "Use Postgres on a remote server"},
    ]
    proposal = reconcile_proposal.build_reconcile_proposal(
        candidates, _existing_store(), source="codex")
    item = proposal["items"][0]
    assert item["action"] == "conflict"
    assert item["match_id"] == "exist-decision-1", "conflicting source id must be preserved"
    assert proposal["counts"]["conflict"] == 1


def test_reconcile_never_applies_and_never_deletes():
    candidates = [
        {"id": "c-novel", "summary": "A brand new and entirely unrelated insight xyz"},
        {"id": "c-dup", "summary": "Always validate at the send boundary"},
        {"id": "c-conflict", "question": "Which database for Engram?",
         "choice": "Use Postgres on a remote server"},
    ]
    proposal = reconcile_proposal.build_reconcile_proposal(
        candidates, _existing_store(), source="cursor")
    # The receipt must never claim it applied anything, and no action is
    # destructive (the only actions are import/duplicate/conflict/skip).
    assert proposal["receipt"]["applied"] is False
    assert set(proposal["counts"]) == {"import", "duplicate", "conflict", "skip"}
    assert proposal["counts"]["conflict"] == 1
    assert proposal["counts"]["duplicate"] == 1
    assert proposal["counts"]["import"] == 1


def test_reconcile_items_are_metadata_only():
    candidates = [{"id": "c1", "summary": "Always validate at the send boundary"}]
    proposal = reconcile_proposal.build_reconcile_proposal(
        candidates, _existing_store(), source="codex")
    blob = json.dumps(proposal, ensure_ascii=False)
    # candidate/existing bodies must not appear — only ids/actions/scores.
    assert "Always validate at the send boundary" not in blob
    allowed = {"candidate_id", "action", "reason", "best_score", "match_id", "entry_type"}
    for item in proposal["items"]:
        assert set(item) <= allowed, set(item) - allowed
