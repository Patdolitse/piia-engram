"""Memory eval suite wrapper tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.run_memory_evals as memory_evals
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
    agent_context_pack = summary["agent_context_pack"]
    assert agent_context_pack["schema"] == "agent_context_pack_eval.v1"
    assert agent_context_pack["public_safe"] is True
    assert agent_context_pack["store_isolated"] is True
    assert agent_context_pack["overall_passed"] is True
    assert agent_context_pack["case_count"] == 2
    assert agent_context_pack["passed_count"] == 2
    assert agent_context_pack["failed_count"] == 0


def test_memory_eval_suite_allows_targeted_agent_context_run() -> None:
    summary = run_suite(recall_fixtures=[], admission_fixtures=[])

    assert summary["recall"] == []
    assert summary["admission"] == []
    assert summary["agent_context_pack"]["overall_passed"] is True


def test_memory_eval_suite_summary_is_metadata_only() -> None:
    summary = run_suite()
    blob = json.dumps(summary, ensure_ascii=False)

    for secret in (
        "SYNTHETIC_SECRET_DO_NOT_LEAK",
        "SYNTHETIC_OLD_AUTH_BODY",
        "without user confirmation",
        "bounded exponential backoff",
        "MCP writes require preview before durable mutation",
        "Governed ack wrapper",
        "candidate should stay under review",
        "FAKE_SK_TOKEN_SENTINEL",
    ):
        assert secret not in blob


def test_memory_eval_suite_goes_red_on_failed_admission_expectation(tmp_path: Path) -> None:
    bad_fixture = tmp_path / "bad_admission.json"
    bad_fixture.write_text(
        json.dumps(
            {
                "schema": 1,
                "guard": "bad_admission_fixture",
                "public_safe": True,
                "existing": [],
                "candidates": [
                    {
                        "id": "C-too-short",
                        "summary": "tiny",
                        "domain": "test",
                        "_expected_action": "accept",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = run_suite(recall_fixtures=[], admission_fixtures=[bad_fixture])

    assert summary["overall_passed"] is False
    assert summary["admission"][0]["failed_expectation_count"] == 1


def test_memory_eval_suite_goes_red_when_agent_context_pack_eval_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        memory_evals,
        "run_agent_context_pack_eval",
        lambda: {
            "schema": "agent_context_pack_eval.v1",
            "overall_passed": False,
            "cases": [
                {"name": "passing-case", "passed": True},
                {"name": "failing-case", "passed": False},
            ],
        },
    )

    summary = run_suite(recall_fixtures=[], admission_fixtures=[])

    assert summary["overall_passed"] is False
    assert summary["agent_context_pack"]["case_count"] == 2
    assert summary["agent_context_pack"]["passed_count"] == 1
    assert summary["agent_context_pack"]["failed_count"] == 1


def test_memory_eval_suite_goes_red_when_agent_context_pack_eval_has_no_cases(monkeypatch) -> None:
    monkeypatch.setattr(
        memory_evals,
        "run_agent_context_pack_eval",
        lambda: {
            "schema": "agent_context_pack_eval.v1",
            "overall_passed": True,
            "cases": [],
        },
    )

    summary = run_suite(recall_fixtures=[], admission_fixtures=[])

    assert summary["overall_passed"] is False
    assert summary["agent_context_pack"]["case_count"] == 0
    assert summary["agent_context_pack"]["passed_count"] == 0
    assert summary["agent_context_pack"]["failed_count"] == 0


def test_memory_eval_suite_public_safe_reflects_agent_context_forbidden_check(monkeypatch) -> None:
    monkeypatch.setattr(
        memory_evals,
        "run_agent_context_pack_eval",
        lambda: {
            "schema": "agent_context_pack_eval.v1",
            "overall_passed": False,
            "cases": [
                {
                    "name": "forbidden-case",
                    "passed": False,
                    "checks": {"no_forbidden_substrings": False},
                }
            ],
        },
    )

    summary = run_suite(recall_fixtures=[], admission_fixtures=[])

    assert summary["public_safe"] is False
    assert summary["agent_context_pack"]["public_safe"] is False
    assert summary["overall_passed"] is False


def test_memory_eval_suite_markdown_is_citable() -> None:
    report = render_markdown(run_suite())

    assert "# Memory Eval Suite v1" in report
    assert "Overall: PASS" in report
    assert "recall_eval_heldout_v1" in report
    assert "admission_guard_heldout_v1" in report
    assert "Forbidden leak" in report
    assert "## Agent Context Pack" in report
    assert "agent_context_pack_eval.v1" in report
    assert "2/2" in report


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
