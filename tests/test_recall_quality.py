"""Recall-quality evaluation (Phase 6).

Loads a fixture corpus with known freshness/provenance metadata (and internal
bookkeeping fields that must never leak) and asserts the recall surface meets a
set of quality invariants. This is the metadata-only evaluation harness called
for in Phase 6: it is a regression guard on *trust* properties (freshness
correctness, no internal-field disclosure, dedup, budget), not a ranking test.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from piia_engram import recall as recall_mod

FIXTURE = Path(__file__).parent / "fixtures" / "recall_quality_corpus.json"


@pytest.fixture(scope="module")
def corpus():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payload(corpus):
    now = datetime.fromisoformat(corpus["now"])
    return recall_mod.build_recall_payload(
        relevant_knowledge=corpus["relevant_knowledge"],
        query_knowledge=corpus["query_knowledge"],
        token_budget=100000,  # large: exercise quality, not trimming
        include_freshness=True,
        now=now,
    )


def _by_id(payload):
    """Map projected items back to a stable key (summary/choice text)."""
    out = {}
    for item in payload["knowledge"]:
        text = item.get("summary") or item.get("choice") or ""
        out[text] = item
    return out


def test_freshness_buckets_match_expected(corpus, payload):
    # Build expected (text -> status) from id -> status + the corpus text.
    id_to_text = {}
    for src in (corpus["relevant_knowledge"], corpus["query_knowledge"]):
        for entry in src:
            text = entry.get("summary") or entry.get("choice") or ""
            id_to_text[entry["id"]] = text
    items = _by_id(payload)
    for eid, status in corpus["expected"]["freshness_by_id"].items():
        text = id_to_text[eid]
        assert text in items, f"{eid} ({text!r}) missing from recall"
        assert items[text]["freshness"]["freshness_status"] == status


def test_no_internal_fields_leak(corpus, payload):
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in corpus["expected"]["forbidden_substrings"]:
        assert forbidden not in blob, f"internal field leaked: {forbidden}"


def test_dedup_collapses_shared_id(corpus, payload):
    # L-dup appears in both buckets but must surface once.
    texts = [i.get("summary") or i.get("choice") for i in payload["knowledge"]]
    dup_text = "Duplicate id appears in both relevant and query buckets"
    assert texts.count(dup_text) == 1


def test_quality_score_high(payload):
    """Aggregate quality: every projected item is explainable.

    Quality = fraction of items that carry a freshness signal. With
    include_freshness=True every item must be annotated, so the score is 1.0.
    """
    knowledge = payload["knowledge"]
    assert knowledge, "corpus should yield knowledge"
    annotated = sum(1 for i in knowledge if isinstance(i.get("freshness"), dict))
    score = annotated / len(knowledge)
    assert score == 1.0


def test_provenance_only_source_explainable(payload):
    # When present, provenance carries only source-explainable keys.
    allowed = {"source_agent", "run_id", "last_validated_at"}
    for item in payload["knowledge"]:
        prov = item.get("provenance")
        if prov is not None:
            assert set(prov).issubset(allowed)


def test_budget_trims_and_reports_excluded(corpus):
    now = datetime.fromisoformat(corpus["now"])
    tiny = recall_mod.build_recall_payload(
        relevant_knowledge=corpus["relevant_knowledge"],
        query_knowledge=corpus["query_knowledge"],
        token_budget=1,  # force trimming
        include_freshness=True,
        now=now,
    )
    # At least one item always survives; the rest are reported as excluded.
    assert len(tiny["knowledge"]) >= 1
    assert tiny["meta"]["governance"]["excluded_count"] >= 1
