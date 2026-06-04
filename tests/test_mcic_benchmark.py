"""Synthetic MCIC benchmark tests.

MCIC = Multi-Client Identity Continuity. The benchmark is intentionally
metadata-only and synthetic: it proves Engram can surface the right continuity
signals across simulated tools, without claiming a live model will always follow
those signals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demos.mcic_benchmark import render_markdown, run_benchmark


def test_mcic_benchmark_passes_ten_purposeful_scenarios(tmp_path: Path) -> None:
    payload = run_benchmark(tmp_path)

    assert payload["schema"] == 1
    assert payload["benchmark"] == "mcic_v1"
    assert payload["isolated_store"] is True
    assert payload["claim"] == "engram_signal_available_not_model_compliance"
    assert payload["scenario_count"] == 10
    assert payload["passed_count"] == 10
    assert payload["failed_count"] == 0
    assert payload["overall_passed"] is True

    categories = {scenario["category"] for scenario in payload["scenarios"]}
    assert categories == {
        "explicit_recall",
        "implicit_personalization",
        "adversarial_guard",
        "safety_boundary",
        "version_chain",
        "negative_control",
        "provenance",
    }
    names = {scenario["name"] for scenario in payload["scenarios"]}
    assert "negative_absent_fact" in names
    assert "version_chain_head_preferred" in names

    for scenario in payload["scenarios"]:
        assert scenario["purpose"]
        assert scenario["source_tool"]
        assert scenario["target_tool"]
        assert scenario["passed"] is True
        assert scenario["evidence_kind"] in {"resume_brief", "search", "direct_context"}
        assert all(scenario["checks"].values())


def test_mcic_benchmark_payload_is_metadata_only(tmp_path: Path) -> None:
    payload = run_benchmark(tmp_path)
    text = json.dumps(payload, ensure_ascii=False)

    assert str(tmp_path) not in text
    assert "mcic-benchmark-" not in text
    assert "MCIC_SECRET_VALUE" not in text
    assert "OLD_MCIC_SUPERSEDED_BODY" not in text
    assert "memory_body" not in text
    assert "raw_path" not in text
    assert "session_id" not in text
    assert "decision_reasoning" not in text
    for scenario in payload["scenarios"]:
        assert set(scenario) == {
            "name",
            "purpose",
            "category",
            "source_tool",
            "target_tool",
            "evidence_kind",
            "checks",
            "passed",
        }


def test_mcic_markdown_report_is_shareable(tmp_path: Path) -> None:
    payload = run_benchmark(tmp_path)
    report = render_markdown(payload)

    assert "# MCIC v1 Benchmark" in report
    assert "Overall: PASS" in report
    assert "Engram signal available, not model compliance" in report
    assert "negative_absent_fact" in report
    assert str(tmp_path) not in report
    assert "MCIC_SECRET_VALUE" not in report
    assert "OLD_MCIC_SUPERSEDED_BODY" not in report
    for marker in ("鈥", "鈫", "\ufffd"):
        assert marker not in report


def test_mcic_json_cli(tmp_path: Path) -> None:
    script = Path("demos") / "mcic_benchmark.py"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["overall_passed"] is True
    assert payload["scenario_count"] == 10
    assert payload["passed_count"] == 10
