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
    assert sum(item["case_count"] for item in summary["recall"]) == 21  # v4.20: +3 real-get_recall playbook cases
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
    pack = summary["agent_context_pack"]
    assert pack["overall_passed"] is True, (
        "agent_context_pack failed; per-case checks: "
        + json.dumps(
            [c for c in pack.get("cases", []) if not c.get("passed")],
            ensure_ascii=False,
        )
    )


def test_memory_eval_suite_agent_context_pack_ignores_live_engram_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    live_store = tmp_path / "live-engram-store"
    live_store.mkdir()
    sentinel = live_store / "sentinel.txt"
    sentinel.write_text("LIVE_MEMORY_SUITE_SENTINEL", encoding="utf-8")
    before_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    monkeypatch.setenv("ENGRAM_DIR", str(live_store))

    summary = memory_evals.run_suite(recall_fixtures=[], admission_fixtures=[])

    after_files = sorted(path.relative_to(live_store).as_posix() for path in live_store.rglob("*"))
    summary_blob = json.dumps(summary, ensure_ascii=False)

    # diagnostic: show per-case check booleans when the pack fails, so the
    # root cause is visible in the CI log (was invisible before 4.18)
    pack = summary.get("agent_context_pack") or {}
    failed_cases = [c for c in pack.get("cases", []) if not c.get("passed")]
    assert summary["overall_passed"] is True, (
        f"agent_context_pack failed; failing cases: "
        f"{json.dumps(failed_cases, ensure_ascii=False)}"
    )
    assert summary["agent_context_pack"]["store_isolated"] is True
    assert before_files == after_files == ["sentinel.txt"]
    assert sentinel.read_text(encoding="utf-8") == "LIVE_MEMORY_SUITE_SENTINEL"
    assert "LIVE_MEMORY_SUITE_SENTINEL" not in summary_blob


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
    )
    if result.returncode != 0:
        # The child stdout carries the full JSON with per-case check booleans;
        # surface it instead of a bare CalledProcessError so an intermittent
        # failure is diagnosable from the log alone.
        failed_cases = ""
        try:
            payload_dbg = json.loads(result.stdout)
            failed_cases = json.dumps(
                payload_dbg.get("agent_context_pack", {}).get("cases", []),
                ensure_ascii=False,
            )
        except Exception:
            failed_cases = result.stdout[-2000:]
        raise AssertionError(
            f"run_memory_evals exited {result.returncode}; "
            f"stderr tail: {result.stderr[-500:]}; cases: {failed_cases}"
        )

    payload = json.loads(result.stdout)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert payload == written
    assert payload["overall_passed"] is True
