"""Tests for the metadata-only knowledge quality evaluator (Task 11).

Drives the evaluator with the high/low quality fixtures so the rejection
criteria stay honest: real candidates pass, mechanically-bad ones are caught.
"""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram import quality_eval as Q

FIX = Path(__file__).resolve().parent / "fixtures" / "quality"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_high_quality_fixtures_all_accepted():
    for entry in _load("high_quality.json"):
        verdict = Q.evaluate_candidate(entry)
        assert verdict["accept"], f"expected accept, got {verdict}"
        assert verdict["reasons"] == []


def test_low_quality_fixtures_rejected_with_expected_reason():
    for entry in _load("low_quality.json"):
        expected = entry.get("_expected_reason")
        verdict = Q.evaluate_candidate(entry)
        assert not verdict["accept"], f"expected reject for {entry.get('summary') or entry.get('question') or entry.get('title')}"
        assert expected in verdict["reasons"], (
            f"expected reason {expected!r} in {verdict['reasons']}"
        )


def test_batch_counts():
    entries = _load("high_quality.json") + _load("low_quality.json")
    result = Q.evaluate_batch(entries)
    assert result["total"] == len(entries)
    assert result["accepted"] == len(_load("high_quality.json"))
    assert result["rejected"] == len(_load("low_quality.json"))


def test_unclassified_is_warning_not_rejection():
    entry = {"summary": "A perfectly reasonable durable lesson with enough length"}
    verdict = Q.evaluate_candidate(entry)
    assert verdict["accept"] is True
    assert "unclassified" in verdict["warnings"]


def test_non_dict_is_rejected():
    verdict = Q.evaluate_candidate("not a dict")  # type: ignore[arg-type]
    assert verdict["accept"] is False
    assert "not_a_dict" in verdict["reasons"]


def test_evaluator_is_metadata_only_does_not_mutate():
    entry = {"summary": "x" * 40, "domain": "y"}
    snapshot = dict(entry)
    Q.evaluate_candidate(entry)
    assert entry == snapshot
