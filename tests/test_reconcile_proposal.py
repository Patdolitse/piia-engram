"""Tests for the dry-run reconciliation proposal/receipt helper (Phase 8)."""

from __future__ import annotations

from piia_engram import reconcile_proposal as rp


EXISTING = [
    {"id": "L1", "summary": "Always run pytest from the E:/Temp virtualenv on this machine"},
    {"id": "D1", "question": "Which mechanism for long phase tasks?",
     "choice": "Use the D+ mechanism by default"},
]


def test_novel_candidate_proposed_for_import():
    cand = {"id": "C1", "summary": "Telemetry payloads must remain metadata only and opt in"}
    out = rp.classify_candidate(cand, EXISTING)
    assert out["action"] == "import"


def test_near_duplicate_detected():
    cand = {"id": "C2", "summary": "Always run pytest from the E:/Temp virtualenv on this machine"}
    out = rp.classify_candidate(cand, EXISTING)
    assert out["action"] == "duplicate"
    assert out["match_id"] == "L1"
    assert out["best_score"] >= rp.DUPLICATE_THRESHOLD


def test_conflict_same_question_different_choice():
    cand = {"id": "C3", "question": "Which mechanism for long phase tasks?",
            "choice": "Use the E+ mechanism with DeepSeek audit"}
    out = rp.classify_candidate(cand, EXISTING)
    assert out["action"] == "conflict"
    assert out["match_id"] == "D1"


def test_same_question_same_choice_is_duplicate_not_conflict():
    cand = {"id": "C4", "question": "Which mechanism for long phase tasks?",
            "choice": "Use the D+ mechanism by default"}
    out = rp.classify_candidate(cand, EXISTING)
    assert out["action"] != "conflict"


def test_build_proposal_counts_and_receipt_never_applies():
    candidates = [
        {"id": "C1", "summary": "Brand new metadata-only telemetry guidance entry"},
        {"id": "C2", "summary": "Always run pytest from the E:/Temp virtualenv on this machine"},
        {"id": "C3", "question": "Which mechanism for long phase tasks?",
         "choice": "Use the E+ mechanism with DeepSeek audit"},
    ]
    proposal = rp.build_reconcile_proposal(candidates, EXISTING, source="claude_memory")
    assert proposal["scanned"] == 3
    assert proposal["counts"]["import"] == 1
    assert proposal["counts"]["duplicate"] == 1
    assert proposal["counts"]["conflict"] == 1
    # The receipt records the dry-run and never claims to have applied anything.
    assert proposal["receipt"]["applied"] is False
    assert proposal["receipt"]["source"] == "claude_memory"


def test_proposal_is_metadata_only():
    candidates = [{"id": "SECRETID", "summary": "SENSITIVE-CANDIDATE-BODY"}]
    proposal = rp.build_reconcile_proposal(candidates, EXISTING)
    blob = repr(proposal)
    assert "SENSITIVE-CANDIDATE-BODY" not in blob
    assert "SECRETID" in blob  # id is metadata; body is not


def test_malformed_candidate_skipped_no_crash():
    proposal = rp.build_reconcile_proposal([None, "str", 5], EXISTING)
    assert proposal["counts"]["skip"] == 3


def test_empty_inputs():
    proposal = rp.build_reconcile_proposal([], [])
    assert proposal["scanned"] == 0
    assert proposal["receipt"]["proposed_import"] == 0
