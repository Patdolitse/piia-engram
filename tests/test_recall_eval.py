"""Offline recall-quality evaluation harness tests.

The harness is intentionally synthetic and ID-scored. It measures whether the
real Engram search surface returns the intended knowledge IDs for frozen query
labels, without touching the user's live store or using a fuzzy LLM judge.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.eval_recall import (
    evaluate_case,
    evaluate_corpus,
    load_corpus,
    render_markdown,
)


FIXTURE = Path(__file__).parent / "fixtures" / "recall_eval_v1.json"


def test_fixture_carries_public_safe_seed_contract() -> None:
    corpus = load_corpus(FIXTURE)

    assert corpus["schema"] == 1
    assert corpus["benchmark"] == "recall_eval_v1"
    assert corpus["public_safe"] is True
    assert corpus["stores"], "at least one synthetic store is required"
    assert len(corpus["cases"]) >= 8
    assert {case["scenario"] for case in corpus["cases"]} >= {
        "exact_lesson",
        "paraphrase_alias",
        "decision_rationale",
        "identity_preference",
        "chinese_query",
        "negative_absent",
        "version_supersession",
        "project_isolation",
    }


def test_evaluate_case_scores_expected_ids_without_text_judge(tmp_path: Path) -> None:
    corpus = load_corpus(FIXTURE)
    case = next(c for c in corpus["cases"] if c["id"] == "decision-rationale")

    result = evaluate_case(corpus, case, tmp_path)

    assert result["case_id"] == "decision-rationale"
    assert result["expected_ids"] == ["D-release-auth"]
    assert result["actual_ids"][0] == "D-release-auth"
    assert result["hit_at_k"] is True
    assert result["recall_at_k"] == pytest.approx(1.0)
    assert result["mrr"] == pytest.approx(1.0)
    assert result["forbidden_leak"] is False
    assert "judge" not in result


def test_negative_case_allows_empty_results_without_false_positive(tmp_path: Path) -> None:
    corpus = load_corpus(FIXTURE)
    case = next(c for c in corpus["cases"] if c["id"] == "negative-absent")

    result = evaluate_case(corpus, case, tmp_path)

    assert result["expected_ids"] == []
    assert result["actual_ids"] == []
    assert result["hit_at_k"] is True
    assert result["precision_at_k"] == pytest.approx(1.0)
    assert result["false_positive"] is False


def test_corpus_summary_has_thresholds_and_no_forbidden_leaks(tmp_path: Path) -> None:
    corpus = load_corpus(FIXTURE)

    summary = evaluate_corpus(corpus, tmp_path)

    assert summary["benchmark"] == "recall_eval_v1"
    assert summary["case_count"] == len(corpus["cases"])
    assert summary["passed_count"] == summary["case_count"]
    assert summary["failed_count"] == 0
    assert summary["overall_passed"] is True
    assert summary["metrics"]["forbidden_leak_rate"] == pytest.approx(0.0)
    assert summary["metrics"]["mean_recall_at_k"] >= summary["thresholds"]["min_mean_recall_at_k"]
    assert summary["metrics"]["mean_mrr"] >= summary["thresholds"]["min_mean_mrr"]
    assert summary["metrics"]["negative_false_positive_rate"] <= summary["thresholds"]["max_negative_false_positive_rate"]


def test_markdown_report_is_shareable_and_metadata_only(tmp_path: Path) -> None:
    corpus = load_corpus(FIXTURE)
    summary = evaluate_corpus(corpus, tmp_path)

    report = render_markdown(summary)

    assert "# Recall Eval v1" in report
    assert "Overall: PASS" in report
    assert str(tmp_path) not in report
    assert "SYNTHETIC_SECRET_DO_NOT_LEAK" not in report
    assert "raw_store_root" not in report
    for case in summary["cases"]:
        assert set(case) == {
            "case_id",
            "scenario",
            "query",
            "expected_ids",
            "forbidden_ids",
            "actual_ids",
            "hit_at_k",
            "precision_at_k",
            "recall_at_k",
            "mrr",
            "forbidden_leak",
            "false_positive",
            "passed",
        }


def test_eval_recall_json_cli(tmp_path: Path) -> None:
    script = Path("scripts") / "eval_recall.py"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("ENGRAM_TEST", None)

    result = subprocess.run(
        [sys.executable, str(script), "--fixture", str(FIXTURE), "--workdir", str(tmp_path), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["overall_passed"] is True
    assert payload["case_count"] >= 8
    assert "DATA FRAGMENTATION" not in result.stderr


def test_eval_recall_default_cli_does_not_leave_repo_tmp() -> None:
    script = Path("scripts") / "eval_recall.py"
    repo_tmp = Path(__file__).resolve().parents[1] / ".tmp"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    before = (
        {path.relative_to(repo_tmp).as_posix() for path in repo_tmp.rglob("*")}
        if repo_tmp.exists()
        else None
    )

    result = subprocess.run(
        [sys.executable, str(script), "--fixture", str(FIXTURE), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["overall_passed"] is True
    after = (
        {path.relative_to(repo_tmp).as_posix() for path in repo_tmp.rglob("*")}
        if repo_tmp.exists()
        else None
    )
    assert after == before
