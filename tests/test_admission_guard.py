"""Admission guard v1 tests.

This is a read-only guard over candidate memories. It combines the existing
metadata-only quality evaluator with duplicate/conflict routing so low-value
or unsafe writes can be reviewed before they pollute durable recall.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.check_admission import evaluate_candidate_admission, evaluate_fixture, load_fixture


FIXTURE = Path(__file__).parent / "fixtures" / "admission_guard_v1.json"
HELDOUT_FIXTURE = Path(__file__).parent / "fixtures" / "admission_guard_heldout_v1.json"


def test_fixture_declares_public_safe_contract() -> None:
    fixture = load_fixture(FIXTURE)

    assert fixture["schema"] == 1
    assert fixture["guard"] == "admission_guard_v1"
    assert fixture["public_safe"] is True
    assert fixture["existing"]
    assert len(fixture["candidates"]) >= 6


def test_heldout_fixture_declares_edge_class_contract() -> None:
    fixture = load_fixture(HELDOUT_FIXTURE)

    assert fixture["schema"] == 1
    assert fixture["guard"] == "admission_guard_heldout_v1"
    assert fixture["public_safe"] is True
    assert fixture["existing"]
    assert len(fixture["candidates"]) >= 8
    assert {candidate["_expected_action"] for candidate in fixture["candidates"]} >= {
        "accept",
        "duplicate",
        "reject",
        "review_update",
        "stage",
    }


def test_rejects_transient_candidate_without_echoing_body() -> None:
    fixture = load_fixture(FIXTURE)
    candidate = next(c for c in fixture["candidates"] if c["id"] == "C-temp-debug")

    result = evaluate_candidate_admission(candidate, fixture["existing"])

    assert result["id"] == "C-temp-debug"
    assert result["action"] == "reject"
    assert "transient_marker" in result["reasons"]
    assert "TODO" not in json.dumps(result, ensure_ascii=False)


def test_routes_duplicates_to_existing_memory() -> None:
    fixture = load_fixture(FIXTURE)
    candidate = next(c for c in fixture["candidates"] if c["id"] == "C-dup-test-first")

    result = evaluate_candidate_admission(candidate, fixture["existing"])

    assert result["action"] == "duplicate"
    assert result["duplicate_of"] == "L-test-first"
    assert result["suggested_action"] == "skip_duplicate"


def test_routes_lesson_conflict_to_update_knowledge_review() -> None:
    fixture = load_fixture(FIXTURE)
    candidate = next(c for c in fixture["candidates"] if c["id"] == "C-conflict-docker")

    result = evaluate_candidate_admission(candidate, fixture["existing"])

    assert result["action"] == "review_update"
    assert result["conflict_with"] == ["L-docker-simple"]
    assert result["suggested_action"] == "update_knowledge"


def test_routes_decision_conflict_to_update_knowledge_review() -> None:
    fixture = load_fixture(FIXTURE)
    candidate = next(c for c in fixture["candidates"] if c["id"] == "C-conflict-release-auth")

    result = evaluate_candidate_admission(candidate, fixture["existing"])

    assert result["action"] == "review_update"
    assert result["conflict_with"] == ["D-release-auth"]
    assert result["suggested_action"] == "update_knowledge"


def test_unclassified_candidate_is_staged_not_rejected() -> None:
    fixture = load_fixture(FIXTURE)
    candidate = next(c for c in fixture["candidates"] if c["id"] == "C-unclassified")

    result = evaluate_candidate_admission(candidate, fixture["existing"])

    assert result["action"] == "stage"
    assert result["reasons"] == []
    assert "unclassified" in result["warnings"]


def test_fixture_summary_matches_expected_actions_and_is_metadata_only() -> None:
    fixture = load_fixture(FIXTURE)

    summary = evaluate_fixture(fixture)

    assert summary["guard"] == "admission_guard_v1"
    assert summary["candidate_count"] == len(fixture["candidates"])
    assert summary["failed_expectations"] == []
    assert summary["action_counts"] == {
        "accept": 1,
        "duplicate": 1,
        "reject": 2,
        "review_update": 2,
        "stage": 1,
    }
    blob = json.dumps(summary, ensure_ascii=False)
    for secret in ("TODO debug", "Always write failing tests", "Docker should always"):
        assert secret not in blob
    for result in summary["results"]:
        assert set(result) <= {
            "id",
            "entry_type",
            "action",
            "suggested_action",
            "reasons",
            "warnings",
            "duplicate_of",
            "conflict_with",
            "expected_action",
        }


def test_heldout_fixture_summary_matches_expected_actions_and_is_metadata_only() -> None:
    fixture = load_fixture(HELDOUT_FIXTURE)

    summary = evaluate_fixture(fixture)

    assert summary["guard"] == "admission_guard_heldout_v1"
    assert summary["candidate_count"] == len(fixture["candidates"])
    assert summary["failed_expectations"] == []
    assert summary["action_counts"] == {
        "accept": 3,
        "duplicate": 1,
        "reject": 1,
        "review_update": 2,
        "stage": 1,
    }
    blob = json.dumps(summary, ensure_ascii=False)
    for secret in ("without user confirmation", "bounded exponential backoff", "without a password"):
        assert secret not in blob
    for result in summary["results"]:
        assert set(result) <= {
            "id",
            "entry_type",
            "action",
            "suggested_action",
            "reasons",
            "warnings",
            "duplicate_of",
            "conflict_with",
            "expected_action",
        }


def test_check_admission_json_cli() -> None:
    script = Path("scripts") / "check_admission.py"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("PYTHONPATH", None)

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
    assert payload["failed_expectations"] == []


def test_check_admission_heldout_json_cli_uses_worktree_source() -> None:
    script = Path("scripts") / "check_admission.py"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--fixture", str(HELDOUT_FIXTURE), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["overall_passed"] is True
    assert payload["failed_expectations"] == []
