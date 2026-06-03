"""Evaluation-set scoring for auto-extraction precision (Node N2).

Runs the durability gate (``_assess_extraction_candidate``) over a fixed
positive/negative corpus and asserts:

- 100% recall on durable engineering value / measured outcomes (no loss),
- 100% precision on short-term reminders/tasks/planning/meta (no false
  positives),
- measured outcomes keep their ``measured_outcome`` signal,
- the assessment is metadata-only (no candidate body echoed back).

The corpus is the regression contract for the smallest-safe precision
improvement (extended ephemeral-reminder lexicon); it makes "fewer false
positives, no loss of valuable outcomes" a measured, repeatable claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram.context import _assess_extraction_candidate

_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "extraction_quality_eval.json").read_text(
        encoding="utf-8"
    )
)


def _assess(case: dict) -> dict:
    ctype = case["type"]
    trigger = "decision_trigger" if ctype == "decision" else "lesson_trigger"
    return _assess_extraction_candidate(case["text"], ctype, trigger)


@pytest.mark.parametrize("case", _CORPUS["positives"], ids=lambda c: c["text"][:30])
def test_positives_are_accepted(case):
    result = _assess(case)
    assert result["accepted"] is True, result
    assert result["signals"], "an accepted candidate must carry a quality signal"
    if case.get("must_keep_signal"):
        assert case["must_keep_signal"] in result["signals"]


@pytest.mark.parametrize("case", _CORPUS["negatives"], ids=lambda c: c["text"][:30])
def test_negatives_are_rejected(case):
    result = _assess(case)
    assert result["accepted"] is False, result
    if case.get("expect_flag"):
        assert case["expect_flag"] in result["flags"], result


def test_eval_set_precision_and_recall_are_perfect():
    pos = [_assess(c)["accepted"] for c in _CORPUS["positives"]]
    neg = [_assess(c)["accepted"] for c in _CORPUS["negatives"]]
    recall = sum(pos) / len(pos)
    false_positives = sum(neg)
    precision = sum(pos) / (sum(pos) + false_positives) if (sum(pos) + false_positives) else 1.0
    assert recall == 1.0, f"lost durable knowledge: {pos}"
    assert false_positives == 0, f"accepted {false_positives} short-term candidate(s)"
    assert precision == 1.0


def test_assessment_is_metadata_only():
    # The gate returns scores/signals/flags — never the candidate body.
    case = _CORPUS["negatives"][0]
    result = _assess(case)
    assert case["text"] not in json.dumps(result, ensure_ascii=False)
    assert set(result) == {
        "accepted", "score", "signals", "flags", "reason",
        "candidate_type", "trigger_reason",
    }
