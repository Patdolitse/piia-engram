"""Contracts and real stdio/hook smoke coverage for the synthetic matrix."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest
from task_checkpoint_harness import hook_command

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuity_matrix", ROOT / "scripts" / "continuity_matrix.py")
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def complete_observation(case, tool="claude_code"):
    text = "\n".join(value for key, value in case["required"].items() if key != "source" and value)
    return {"resume": {"markdown": text + "\n" + tool +
        "\n- **source**: session_digest; earlier session record; unreviewed; no approval or action authority"},
        "recall": {}}


def test_preregistered_cartesian_dimensions():
    cases = matrix.load_cases()
    assert {case["id"] for case in cases} == {"cold_start", "restart_compaction", "git_worktrees",
        "same_name_isolation", "duplicate_events", "store_locked", "store_unwritable", "source_attribution", "chinese_recall"}
    assert len(cases) * 2 * 2 * 2 == 72
    for case in cases:
        assert set(case["required"]) == set(matrix.FIELDS)
        assert all(case["required"].values())


@pytest.mark.parametrize("field", matrix.FIELDS)
def test_checker_rejects_each_missing_required_field(field):
    case = matrix.load_cases()[0]
    observation = complete_observation(case)
    assert matrix.score_observation(case, observation, "claude_code")["continuation_complete"]
    needle = "claude_code" if field == "source" else case["required"][field]
    observation["resume"]["markdown"] = observation["resume"]["markdown"].replace(needle, "")
    score = matrix.score_observation(case, observation, "claude_code")
    assert not score["continuation_complete"]
    assert field in score["missing_fields"]


def test_absent_fields_have_no_obligation_and_do_not_count_as_recovered():
    case = copy.deepcopy(matrix.load_cases()[0])
    case["required"]["blocker"] = None
    score = matrix.score_observation(case, complete_observation(case), "claude_code")
    assert score["continuation_complete"]
    assert "blocker" not in score["fields"]


@pytest.mark.parametrize("token", (*matrix.FORBIDDEN, "CM_NEW_PROPOSAL"))
def test_checker_catches_wrong_adoption_on_each_receiving_surface(token):
    case = matrix.load_cases()[0]
    for surface in ("resume", "recall"):
        observation = complete_observation(case)
        if surface == "resume":
            observation["resume"]["markdown"] += token
        else:
            observation["recall"] = {"knowledge": [{"text": token}]}
        score = matrix.score_observation(case, observation, "claude_code")
        assert score["wrong_adoption"] == 1
        assert score["wrong_adoption_markers"] == [token]


def test_written_source_line_cannot_replace_native_attribution_warning():
    case = matrix.load_cases()[0]
    observation = complete_observation(case)
    observation["resume"]["markdown"] = observation["resume"]["markdown"].replace("unreviewed", "approved")
    assert not matrix.score_observation(case, observation, "claude_code")["fields"]["source"]


def test_checker_ignores_requests_and_fixture_metadata():
    case = matrix.load_cases()[0]
    observation = {"resume": {"expected": complete_observation(case)["resume"]["markdown"]},
                   "request": case["required"], "source_fixture": case["required"],
                   "recall": {"meta": case["required"]}}
    assert not matrix.score_observation(case, observation, "claude_code")["continuation_complete"]


def test_chinese_fields_are_checked_literally():
    case = matrix.load_cases()[-1]
    observation = complete_observation(case, "cursor")
    assert matrix.score_observation(case, observation, "cursor")["continuation_complete"]
    observation["resume"]["markdown"] = observation["resume"]["markdown"].replace("字段顺序错误", "未知")
    assert not matrix.score_observation(case, observation, "cursor")["fields"]["last_failure_reason"]


def test_child_environment_does_not_inherit_store_or_client_hints(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("CURSOR_PROJECT_DIR", "do-not-inherit")
    monkeypatch.setenv("ENGRAM_CRYPTO", "do-not-inherit")
    monkeypatch.setenv("GIT_DIR", "do-not-inherit")
    monkeypatch.setenv("GIT_WORK_TREE", "do-not-inherit")
    env = matrix.isolated_env(tmp_path / "profile", tmp_path / "store", "default")
    for key in ("ENGRAM_DIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TEMP", "TMP", "TMPDIR"):
        assert Path(env[key]).is_relative_to(tmp_path)
    assert env["ENGRAM_APPROVAL"] == "default"
    assert "CURSOR_PROJECT_DIR" not in env and "ENGRAM_CRYPTO" not in env
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["DO_NOT_TRACK"] == env["ENGRAM_NO_UPDATE_CHECK"] == env["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.fixture(scope="module")
def cold_rows(tmp_path_factory):
    base = tmp_path_factory.mktemp("continuity-matrix") / "fresh"
    result = asyncio.run(matrix.run_matrix(base, cases=[matrix.load_cases()[0]]))
    assert result["error_count"] == 0
    return result["rows"]


def test_real_manual_stdio_continues_in_both_directions_and_modes(cold_rows):
    rows = [row for row in cold_rows if row["integration"] == "manual_mcp"]
    assert len(rows) == 4
    for row in rows:
        assert row["accepted"] and all(row["checks"].values()), row


def test_hooks_queue_without_claiming_automatic_consumption(cold_rows):
    for row in cold_rows:
        if row["integration"] == "automatic_hooks":
            assert row["checks"]["hook_queued"]
            assert not row["direct"]["continuation_complete"]
            assert not row["automatic_end_to_end"]
            assert not row["accepted"]


def test_cursor_payload_recovers_after_explicit_processor_and_start_hook(cold_rows):
    rows = [row for row in cold_rows if row["integration"] == "automatic_hooks" and row["direction"].startswith("Client Y")]
    assert len(rows) == 2
    for row in rows:
        assert row["recoverable_after_drain"]
        assert row["hook_injection_after_drain"]["continuation_complete"]


@pytest.mark.parametrize("approval", ["default", "strict"])
@pytest.mark.xfail(strict=True, reason="Opted-out Stop retains the activity-only gap; explicit default-off negative witness")
def test_claude_stop_preserves_required_task_fields(cold_rows, approval):
    row = next(row for row in cold_rows if row["integration"] == "automatic_hooks"
        and row["direction"].startswith("Client X") and row["approval"] == approval)
    assert row["after_drain"]["continuation_complete"], row["after_drain"]["missing_fields"]


@pytest.fixture(scope="module")
def enabled_rows(tmp_path_factory):
    base = tmp_path_factory.mktemp("continuity-enabled") / "fresh"
    result = asyncio.run(matrix.run_matrix(base, cases=[matrix.load_cases()[0]], task_checkpoint=True,
        capture_hook_command=hook_command(read_delay=0.25)))
    assert result["capture_success_seam"]
    assert result["error_count"] == 0
    return result["rows"]


@pytest.mark.parametrize("approval", ["default", "strict"])
def test_enabled_claude_stop_preserves_required_task_fields(enabled_rows, approval):
    row = next(row for row in enabled_rows if row["integration"] == "automatic_hooks"
        and row["direction"].startswith("Client X") and row["approval"] == approval)
    assert row["after_drain"]["continuation_complete"], row["after_drain"]["missing_fields"]
    assert row["hook_injection_after_drain"]["continuation_complete"]
    assert row["recoverable_after_drain"]
    assert not row["direct"]["continuation_complete"] and not row["automatic_end_to_end"]
    assert row["wrong_adoption"] == 0
    assert row["owner_review_items"] == (2 if approval == "strict" else 1)
    assert row["model_calls"] == row["model_cost"] == 0


def test_no_wrong_adoption_and_review_cost_are_separate_from_model_cost(cold_rows):
    for row in cold_rows:
        assert row["wrong_adoption"] == 0
        assert row["owner_review_items"] == (2 if row["approval"] == "strict" else 1)
        assert row["model_calls"] == row["model_cost"] == 0
        assert all(step["latency_ms"] >= 0 for step in row["steps"])


def test_existing_work_directory_is_refused(tmp_path):
    with pytest.raises(FileExistsError):
        asyncio.run(matrix.run_matrix(tmp_path))
