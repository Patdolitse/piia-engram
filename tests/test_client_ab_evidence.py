"""Tests for the offline synthetic client A/B evidence harness (Task 1, C+).

These assert the harness's promises: the Engram-on arm surfaces strictly more
knowledge signals than the Engram-off arm, neither copied-store arm is mutated,
the synthetic "live" store fingerprint is untouched, the report carries no raw
bodies/paths, output is byte-stable across temp dirs, and the public-safe
summary cannot overclaim live-agent behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import client_ab_evidence_harness as ab  # noqa: E402
from piia_engram import client_validation as cv  # noqa: E402


def test_signal_differential_is_positive(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base")
    assert report["on_arm"]["surfaced_signal_count"] > 0
    assert report["off_arm"]["surfaced_signal_count"] == 0
    assert report["signal_differential"] > 0
    assert report["differential_positive"] is True


def test_both_arms_zero_pollution_and_live_untouched(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base")
    assert report["on_arm"]["zero_pollution_clean"] is True
    assert report["off_arm"]["zero_pollution_clean"] is True
    assert report["arms_zero_pollution_clean"] is True
    assert report["live_store_untouched"] is True
    assert report["overall_passed"] is True


def test_report_declares_offline_invariants(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base")
    assert report["synthetic_only"] is True
    assert report["live_provider_auth"] is False
    assert report["network_used"] is False


def test_report_is_metadata_only(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base")
    blob = json.dumps(report, ensure_ascii=False)
    # No synthetic knowledge bodies leak.
    assert "synthetic lesson alpha" not in blob
    assert "synthetic choice one" not in blob
    assert "synthetic question one" not in blob
    # No absolute temp paths leak into the report.
    assert str(tmp_path) not in blob


def test_output_is_byte_stable_across_temp_dirs(tmp_path: Path):
    a = ab.run_harness(tmp_path / "a")
    b = ab.run_harness(tmp_path / "b")
    # Drop the nested public_summary (identical anyway) and compare full reports.
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
        b, ensure_ascii=False, sort_keys=True
    )


def test_tree_digest_is_path_independent(tmp_path: Path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "x" / "f.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "y" / "f.json").write_text('{"a": 1}', encoding="utf-8")
    assert cv.tree_digest(cv.snapshot_tree(tmp_path / "x")) == cv.tree_digest(
        cv.snapshot_tree(tmp_path / "y")
    )


def test_public_summary_blocks_live_overclaim(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base", client_id="openclaw")
    summary = cv.build_public_safe_summary(
        report,
        claimed_level="L4",
        claim="OpenClaw live agent continuity verified",
        evidence_mode="static offline A/B",
    )
    assert summary["claim_allowed"] is False
    assert summary["claim_problems"]


def test_public_summary_allows_honest_static_claim(tmp_path: Path):
    report = ab.run_harness(tmp_path / "base", client_id="hermes")
    summary = cv.build_public_safe_summary(report)
    assert summary["claim_allowed"] is True
    assert summary["live_provider_auth"] is False
    assert summary["signal_differential"] == report["signal_differential"]


def test_build_ab_evidence_flags_polluted_arm():
    on_arm = {"surfaced_signal_count": 3, "zero_pollution_clean": True}
    polluted_off = {"surfaced_signal_count": 0, "zero_pollution_clean": False}
    evidence = cv.build_ab_evidence(on_arm=on_arm, off_arm=polluted_off)
    assert evidence["arms_zero_pollution_clean"] is False
    assert evidence["overall_passed"] is False


def test_build_ab_evidence_flags_live_store_mutation():
    on_arm = {"surfaced_signal_count": 3, "zero_pollution_clean": True}
    off_arm = {"surfaced_signal_count": 0, "zero_pollution_clean": True}
    evidence = cv.build_ab_evidence(
        on_arm=on_arm,
        off_arm=off_arm,
        live_store_digest_before="AAAA",
        live_store_digest_after="BBBB",
    )
    assert evidence["live_store_untouched"] is False
    assert evidence["overall_passed"] is False


def test_no_differential_fails_overall():
    arm = {"surfaced_signal_count": 0, "zero_pollution_clean": True}
    evidence = cv.build_ab_evidence(on_arm=arm, off_arm=arm)
    assert evidence["differential_positive"] is False
    assert evidence["overall_passed"] is False
