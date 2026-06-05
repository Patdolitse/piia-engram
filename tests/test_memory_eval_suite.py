"""Memory eval suite wrapper tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.run_memory_evals import render_markdown, run_suite


def test_memory_eval_suite_runs_baseline_and_heldout_sets() -> None:
    summary = run_suite()

    assert summary["schema"] == 1
    assert summary["suite"] == "memory_eval_suite_v1"
    assert summary["public_safe"] is True
    assert summary["overall_passed"] is True
    assert {item["benchmark"] for item in summary["recall"]} == {
        "recall_eval_v1",
        "recall_eval_heldout_v1",
    }
    assert {item["guard"] for item in summary["admission"]} == {
        "admission_guard_v1",
        "admission_guard_heldout_v1",
    }
    for item in summary["recall"]:
        assert item["forbidden_leak_rate"] == 0.0
        assert item["negative_false_positive_rate"] == 0.0
    assert sum(item["case_count"] for item in summary["recall"]) == 18
    assert sum(item["candidate_count"] for item in summary["admission"]) == 15


def test_memory_eval_suite_summary_is_metadata_only() -> None:
    summary = run_suite()
    blob = json.dumps(summary, ensure_ascii=False)

    for secret in (
        "SYNTHETIC_SECRET_DO_NOT_LEAK",
        "SYNTHETIC_OLD_AUTH_BODY",
        "without user confirmation",
        "bounded exponential backoff",
    ):
        assert secret not in blob


def test_memory_eval_suite_markdown_is_citable() -> None:
    report = render_markdown(run_suite())

    assert "# Memory Eval Suite v1" in report
    assert "Overall: PASS" in report
    assert "recall_eval_heldout_v1" in report
    assert "admission_guard_heldout_v1" in report
    assert "Forbidden leak" in report


def test_memory_eval_suite_cli_json_and_output_file(tmp_path: Path) -> None:
    script = Path("scripts") / "run_memory_evals.py"
    output = tmp_path / "memory-evals.json"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--json", "--output", str(output)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert payload == written
    assert payload["overall_passed"] is True
