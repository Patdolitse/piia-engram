"""Synthetic cross-tool resume quality benchmark tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demos.cross_tool_resume_benchmark import render_markdown, run_benchmark, _score_brief


def test_cross_tool_resume_benchmark_rubric_can_fail() -> None:
    rubric = _score_brief(
        "The next action is to run focused tests before publication.",
        {
            "present": ["focused tests"],
            "missing": ["custom ENGRAM_DIR"],
        },
    )

    assert rubric == {"present": True, "missing": False}


def test_cross_tool_resume_benchmark_passes_all_scenarios(tmp_path: Path) -> None:
    payload = run_benchmark(tmp_path)

    assert payload["schema"] == 1
    assert payload["benchmark"] == "cross_tool_resume_quality"
    assert payload["isolated_store"] is True
    assert payload["scenario_count"] == 3
    assert payload["passed_count"] == 3
    assert payload["failed_count"] == 0
    assert payload["overall_passed"] is True

    names = {item["name"] for item in payload["scenarios"]}
    assert names == {
        "mid_refactor_handoff",
        "decision_context_handoff",
        "cold_resume_after_pause",
    }
    for scenario in payload["scenarios"]:
        assert scenario["passed"] is True
        assert scenario["path_redaction_ok"] is True
        assert all(scenario["rubric"].values())
        assert "handoff" in scenario["sections_included"]
        assert "identity" in scenario["sections_included"]


def test_cross_tool_resume_benchmark_report_redacts_paths(tmp_path: Path) -> None:
    payload = run_benchmark(tmp_path)
    report = render_markdown(payload)

    assert "Cross-Tool Resume Quality Benchmark" in report
    assert "Overall: PASS" in report
    assert "<benchmark-project>" in report
    assert str(tmp_path) not in report
    assert "synthetic-project" not in report
    assert "ZZ_" not in report
    for marker in ("鈥", "鈫", "\ufffd"):
        assert marker not in report


def test_cross_tool_resume_benchmark_json_cli(tmp_path: Path) -> None:
    script = Path("demos") / "cross_tool_resume_benchmark.py"
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
    assert payload["scenario_count"] == 3
    assert payload["passed_count"] == 3
