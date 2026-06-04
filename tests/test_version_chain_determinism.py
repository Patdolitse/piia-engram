"""Tests for the version-chain & supersession determinism harness (Task 2, C+).

Pin the harness's promises: byte-stable output across runs against a frozen
golden, superseded ids never surface as HEAD/current, and a deliberately
degraded collapse (one that leaks a superseded id) is caught.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import version_chain_determinism_harness as harness  # noqa: E402

_GOLDEN = _ROOT / "tests" / "snapshots" / "version_chain_determinism_golden.json"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def test_matches_frozen_golden():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert _canon(harness.run_harness()) == _canon(golden)


def test_output_is_deterministic_across_runs():
    assert _canon(harness.run_harness()) == _canon(harness.run_harness())


def test_corpus_fingerprint_is_stable():
    a = harness.run_harness()["corpus_fingerprint"]
    b = harness.run_harness()["corpus_fingerprint"]
    assert a == b and len(a) == 64


def test_superseded_ids_never_surface_as_head():
    report = harness.run_harness()
    superseded = set(report["superseded_ids"])
    heads = set(report["head_ids"])
    assert superseded
    assert not (superseded & heads)
    assert report["invariants"]["superseded_never_head"] is True


def test_collapse_hides_exactly_the_superseded():
    report = harness.run_harness()
    assert set(report["recall_collapse"]["collapsed_ids"]) == set(report["superseded_ids"])
    kept = set(report["recall_collapse"]["kept_ids"])
    assert not (kept & set(report["superseded_ids"]))


def test_report_is_metadata_only():
    report = harness.run_harness()
    blob = json.dumps(report, ensure_ascii=False)
    # No content fields - only ids/counts/booleans.
    assert "summary" not in blob
    assert "choice" not in blob


def test_degraded_corpus_changes_fingerprint_and_breaks_golden():
    # Drop one supersedes edge; a previously-superseded id becomes a HEAD.
    degraded_edges = [
        e for e in harness._SYNTHETIC_EDGES if not (e["src"] == "a3" and e["dst"] == "a2")
    ]
    report = harness.run_harness(edges=degraded_edges)
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert report["corpus_fingerprint"] != golden["corpus_fingerprint"]
    # a2 is no longer superseded by a3, so it now surfaces as a HEAD.
    assert "a2" in report["head_ids"]
    assert "a2" not in report["superseded_ids"]


def test_degraded_collapse_keeping_superseded_is_caught():
    """A buggy collapse that keeps a superseded id must fail the invariant."""
    report = harness.run_harness()
    kept = list(report["recall_collapse"]["kept_ids"]) + [report["superseded_ids"][0]]
    invariants = harness.evaluate_invariants(
        heads=report["head_ids"],
        superseded=report["superseded_ids"],
        kept_ids=kept,
        collapsed_ids=report["recall_collapse"]["collapsed_ids"],
    )
    assert invariants["no_superseded_in_kept"] is False
