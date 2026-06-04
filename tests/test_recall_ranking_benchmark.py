"""Tests for the recall-ranking reproducibility benchmark (Task 3, C+).

Pin the benchmark's promises: the same frozen corpus produces byte-identical
scores against a golden, ordering is stable regardless of input order, and a
deliberately degraded ranker (or a corpus missing the expected hit) drops hit@k
so a ranking regression is caught.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import recall_ranking_benchmark as bench  # noqa: E402

_GOLDEN = _ROOT / "tests" / "snapshots" / "recall_ranking_golden.json"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def test_matches_frozen_golden():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert _canon(bench.run_benchmark()) == _canon(golden)


def test_scores_are_reproducible_across_runs():
    assert _canon(bench.run_benchmark()) == _canon(bench.run_benchmark())


def test_baseline_is_perfect_hit_at_1():
    report = bench.run_benchmark()
    assert report["hit_at_1"] == report["query_count"]
    assert report["mrr"] == 1.0
    assert report["overall_passed"] is True


def test_declares_no_store_or_remote_embeddings():
    report = bench.run_benchmark()
    assert report["store_access"] is False
    assert report["remote_embeddings"] is False
    assert report["synthetic_only"] is True


def test_ordering_is_input_order_independent():
    forward = bench.run_benchmark()
    shuffled_corpus = list(reversed(bench._CORPUS))
    reversed_run = bench.run_benchmark(corpus=shuffled_corpus)
    # Per-query ranked id+score lists must be identical regardless of corpus order.
    f_rows = {r["query"]: r["ranked"] for r in forward["rows"]}
    r_rows = {r["query"]: r["ranked"] for r in reversed_run["rows"]}
    assert f_rows == r_rows


def test_degraded_constant_ranker_loses_hits():
    """A ranker that returns a constant cannot discriminate → hit@1 collapses."""
    report = bench.run_benchmark(scorer=lambda item, terms: 1.0)
    assert report["hit_at_1"] < report["query_count"]
    assert report["overall_passed"] is False


def test_missing_expected_hit_is_caught():
    """Dropping the expected item for a query must make that query miss."""
    corpus = [item for item in bench._CORPUS if item["id"] != "K1"]
    report = bench.run_benchmark(corpus=corpus)
    k1_row = next(r for r in report["rows"] if r["expected"] == "K1")
    assert k1_row["hit_at_1"] is False
    assert k1_row["rank_of_expected"] == 0
    assert report["overall_passed"] is False


def test_report_is_metadata_only():
    report = bench.run_benchmark()
    blob = json.dumps(report, ensure_ascii=False)
    # Rows carry only ids/scores. The report echoes the *query* strings (public
    # inputs), but never a stored knowledge body. Assert on body-only tokens that
    # appear in corpus summaries/choices but in no query.
    assert "best practices" not in blob
    assert "publishing" not in blob
    assert "registration" not in blob
