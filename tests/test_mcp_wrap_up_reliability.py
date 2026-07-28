from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram import mcp_server


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def isolated_mcp_engram(tmp_path: Path, monkeypatch) -> Engram:
    eng = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_tracker", None)
    return eng


def test_wrap_up_session_skips_reconciliation_by_default(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def reconcile_memories():
        calls.append("mem")
        raise AssertionError("reconcile_memories should not run by default")

    def reconcile_ai_configs():
        calls.append("cfg")
        raise AssertionError("reconcile_ai_configs should not run by default")

    monkeypatch.setattr(isolated_mcp_engram, "reconcile_memories", reconcile_memories)
    monkeypatch.setattr(isolated_mcp_engram, "reconcile_ai_configs", reconcile_ai_configs)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Finished M8 reliability planning.",
        source_tool="codex",
        user_confirmed=True,
    )))

    assert calls == []
    maintenance = payload["maintenance"]
    assert maintenance["reconcile_memories"]["status"] == "skipped"
    assert maintenance["reconcile_ai_configs"]["status"] == "skipped"


def test_wrap_up_session_can_run_reconciliation_when_explicitly_requested(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_memories",
        lambda: calls.append("mem") or {"imported": 0, "sources": []},
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_ai_configs",
        lambda: calls.append("cfg") or {"imported": 0, "sources": [], "scanned_files": 0},
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Finished M8 reliability planning.",
        source_tool="codex",
        user_confirmed=True,
        run_reconcile=True,
    )))

    assert calls == ["mem", "cfg"]
    assert payload["maintenance"]["reconcile_memories"]["status"] == "ok"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "ok"


def test_wrap_up_session_returns_metadata_only_timing(
    isolated_mcp_engram: Engram,
) -> None:
    secret = "sk-" + "a" * 32

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary=f"Remember to avoid leaking {secret}",
        source_tool="codex",
        user_confirmed=True,
    )))

    timing = payload["timing"]
    assert isinstance(timing["total_ms"], int)
    assert timing["total_ms"] >= 0
    assert "extract_session_insights_ms" in timing
    assert "append_daily_log_ms" in timing
    assert secret not in json.dumps(timing, ensure_ascii=False)


def test_wrap_up_session_daily_tally_handles_numeric_counts(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        isolated_mcp_engram,
        "extract_session_insights",
        lambda *args, **kwargs: {"saved_lessons": 2, "saved_decisions": 1, "results": []},
    )

    def append_daily_log(project_folder: str, content: str, event_type: str, source_tool: str):
        captured["content"] = content
        return {"file": "daily.md", "created": True}

    monkeypatch.setattr(isolated_mcp_engram, "append_daily_log", append_daily_log)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Session ended cleanly.",
        source_tool="codex",
        user_confirmed=True,
    )))

    assert "lessons=2" in captured["content"]
    assert "decisions=1" in captured["content"]
    assert payload["daily_log"]["file"] == "daily.md"


def test_wrap_up_session_default_without_opt_in_does_not_send_remote_feedback(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    sent: list[str] = []

    monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
    monkeypatch.delenv("ENGRAM_FEEDBACK", raising=False)

    import piia_engram.telemetry as telemetry

    def fail_urlopen(*args, **kwargs):
        sent.append("urlopen")
        raise AssertionError("default closeout should not send remote data")

    monkeypatch.setattr(telemetry, "urlopen", fail_urlopen)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Finished local-only boundary smoke.",
        source_tool="codex",
        user_confirmed=True,
    )))

    assert sent == []
    assert payload["maintenance"]["reconcile_memories"]["status"] == "skipped"


def test_wrap_up_session_fast_mode_skips_extraction_and_late_optional_stages(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGRAM_WRAP_UP_MODE", "fast")
    calls: list[str] = []

    monkeypatch.setattr(
        isolated_mcp_engram,
        "extract_session_insights",
        lambda *a, **k: calls.append("insights") or {"saved_lessons": 1},
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "extract_playbook_from_session",
        lambda *a, **k: calls.append("playbook") or None,
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "get_staging_summary",
        lambda: calls.append("staging") or {"total_staging": 0},
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Fast closeout should not run extraction-heavy stages.",
        source_tool="codex",
        user_confirmed=True,
    )))

    assert calls == []
    assert payload["maintenance"]["closeout_mode"] == "fast"
    assert payload["maintenance"]["extract_session_insights"]["status"] == "skipped"
    assert payload["maintenance"]["extract_playbook_from_session"]["status"] == "skipped"
    assert payload["maintenance"]["reconcile_memories"]["status"] == "skipped"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "skipped"
    assert payload["maintenance"]["evaluate_tiers"]["status"] == "skipped"
    assert payload["maintenance"]["staging_summary"]["status"] == "skipped"
    assert payload["maintenance"]["telemetry_flush"]["status"] == "skipped"
    assert payload["maintenance"]["feedback_send"]["status"] == "skipped"


def test_wrap_up_session_budget_metadata_is_path_free(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGRAM_WRAP_UP_MAX_MS", "1")

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Budget metadata should stay metadata-only.",
        source_tool="codex",
        project_folder="/tmp/Workspace With Spaces/project",
        user_confirmed=True,
    )))
    body = json.dumps(payload.get("maintenance", {}), ensure_ascii=False)

    assert "Workspace With Spaces" not in body
    assert "<path>" not in body
    assert payload["maintenance"]["budget"]["budget_ms"] == 1


def test_wrap_up_session_explicit_reconcile_not_skipped_by_fast_mode(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENGRAM_WRAP_UP_MODE", "fast")
    calls: list[str] = []
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_memories",
        lambda: calls.append("mem") or {"imported": 0, "sources": []},
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_ai_configs",
        lambda: calls.append("cfg") or {"imported": 0, "sources": [], "scanned_files": 0},
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Explicit reconcile must remain explicit even in fast mode.",
        source_tool="codex",
        user_confirmed=True,
        run_reconcile=True,
    )))

    assert calls == ["mem", "cfg"]
    assert payload["maintenance"]["reconcile_memories"]["status"] == "ok"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "ok"


def test_wrap_up_session_allows_explicit_global_reconcile(
    isolated_mcp_engram: Engram,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, dict]] = []

    def reconcile_memories(**kwargs):
        calls.append(("mem", kwargs))
        return {"imported": 0, "sources": []}

    def reconcile_ai_configs(**kwargs):
        calls.append(("cfg", kwargs))
        return {
            "imported": 0,
            "sources": [],
            "scanned_files": 0,
            "budget_exhausted": False,
        }

    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_memories",
        reconcile_memories,
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_ai_configs",
        reconcile_ai_configs,
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Owner explicitly requested a global reconcile.",
        source_tool="codex",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=True,
        reconcile_scope="global",
    )))

    assert calls == [("mem", {}), ("cfg", {})]
    assert payload["maintenance"]["reconcile_scope"] == {
        "requested": "global",
        "effective": "global",
        "project_scoped": False,
    }


def test_wrap_up_session_reports_config_budget_as_partial_completion(
    isolated_mcp_engram: Engram,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_memories",
        lambda **kwargs: {
            "imported": 0,
            "sources": [],
            "scope": {"mode": "project_exact"},
        },
    )
    monkeypatch.setattr(
        isolated_mcp_engram,
        "reconcile_ai_configs",
        lambda **kwargs: {
            "imported": 25,
            "sources": [],
            "scanned_files": 4,
            "budget_exhausted": True,
            "scope": {"mode": "project_exact"},
        },
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Project reconcile reached its bounded import budget.",
        source_tool="codex",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=True,
    )))

    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "partial"
    assert payload["maintenance"]["reconcile_ai_configs"]["budget_exhausted"] is True
    assert payload["operation"]["status"] == "partial_complete"
    assert payload["operation"]["stages"]["reconcile_ai_configs"]["status"] == "partial"
    assert payload["operation"]["outcome"]["stage_partials"] == [
        "reconcile_ai_configs"
    ]


def test_wrap_up_session_returns_queryable_operation_status(
    isolated_mcp_engram: Engram,
) -> None:
    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Completed closeout operation status wiring.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="status-query-smoke",
    )))

    operation = payload["operation"]
    assert operation["operation_id"].startswith("wrap-")
    assert operation["status"] == "completed"
    assert operation["stages"]["append_daily_log"]["status"] == "ok"
    assert "Completed closeout operation status wiring" not in json.dumps(
        operation,
        ensure_ascii=False,
    )

    queried = json.loads(_run(mcp_server.get_wrap_up_session_status(
        operation["operation_id"],
    )))
    assert queried["operation_id"] == operation["operation_id"]
    assert queried["status"] == "completed"
    assert queried["outcome"]["daily_log_written"] is True


def test_wrap_up_session_status_can_be_recovered_by_idempotency_key(
    isolated_mcp_engram: Engram,
) -> None:
    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Caller-known key must recover status after a lost response.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="lost-response-recovery",
    )))

    queried = json.loads(_run(mcp_server.get_wrap_up_session_status(
        idempotency_key="lost-response-recovery",
    )))

    assert queried["operation_id"] == payload["operation"]["operation_id"]
    assert queried["status"] == "completed"


def test_wrap_up_session_status_rejects_mismatched_identifiers(
    isolated_mcp_engram: Engram,
) -> None:
    payload = json.loads(_run(mcp_server.get_wrap_up_session_status(
        operation_id="wrap-00000000000000000000000000000000",
        idempotency_key="different-operation",
    )))

    assert payload["status"] == "error"
    assert payload["error"] == "operation_id does not match idempotency_key"


def test_wrap_up_session_status_projects_stale_running_without_writing(
    isolated_mcp_engram: Engram,
) -> None:
    from piia_engram.session_closeout import begin_wrap_up_operation

    state, replay = begin_wrap_up_operation(
        isolated_mcp_engram.root,
        idempotency_key="stale-running-recovery",
        source_tool="codex",
        budget_ms=1,
        closeout_mode="standard",
    )
    assert replay is False
    operation_file = (
        isolated_mcp_engram.root
        / "operations"
        / "wrap_up_session"
        / f"{state['operation_id']}.json"
    )
    stored = json.loads(operation_file.read_text(encoding="utf-8"))
    stored["updated_at"] = "2000-01-01T00:00:00Z"
    operation_file.write_text(json.dumps(stored), encoding="utf-8")
    before = operation_file.read_bytes()

    queried = json.loads(_run(mcp_server.get_wrap_up_session_status(
        idempotency_key="stale-running-recovery",
    )))

    assert queried["status"] == "stale_running"
    assert queried["persisted_status"] == "running"
    assert queried["diagnostics"]["possibly_interrupted"] is True
    assert operation_file.read_bytes() == before


def test_idempotency_key_begin_is_atomic_across_threads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    from piia_engram import session_closeout

    original_write = session_closeout._write_wrap_up_operation

    def delayed_write(root, state):
        time.sleep(0.05)
        return original_write(root, state)

    monkeypatch.setattr(
        session_closeout,
        "_write_wrap_up_operation",
        delayed_write,
    )

    def begin():
        return session_closeout.begin_wrap_up_operation(
            tmp_path,
            idempotency_key="concurrent-closeout-key",
            source_tool="codex",
            budget_ms=30_000,
            closeout_mode="standard",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: begin(), range(8)))

    assert sum(not replay for _state, replay in results) == 1
    assert sum(replay for _state, replay in results) == 7
    assert len({state["operation_id"] for state, _replay in results}) == 1


def test_idempotent_replay_projects_stale_status_at_top_level(
    isolated_mcp_engram: Engram,
) -> None:
    from piia_engram.session_closeout import begin_wrap_up_operation

    state, replay = begin_wrap_up_operation(
        isolated_mcp_engram.root,
        idempotency_key="stale-replay-key",
        source_tool="codex",
        budget_ms=1,
        closeout_mode="standard",
    )
    assert replay is False
    operation_file = (
        isolated_mcp_engram.root
        / "operations"
        / "wrap_up_session"
        / f"{state['operation_id']}.json"
    )
    stored = json.loads(operation_file.read_text(encoding="utf-8"))
    stored["updated_at"] = "2000-01-01T00:00:00Z"
    operation_file.write_text(json.dumps(stored), encoding="utf-8")

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Retry observes the existing stale operation.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="stale-replay-key",
    )))

    assert payload["status"] == "stale_running"
    assert payload["operation"]["status"] == "stale_running"
    assert payload["idempotent_replay"] is True


def test_wrap_up_session_idempotency_key_replay_does_not_duplicate_writes(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    calls = {"extract": 0, "daily": 0}

    def extract_session_insights(*args, **kwargs):
        calls["extract"] += 1
        return {"saved_lessons": 1, "saved_decisions": 0, "results": []}

    def append_daily_log(project_folder: str, content: str, event_type: str, source_tool: str):
        calls["daily"] += 1
        return {"file": "daily.md", "created": calls["daily"] == 1}

    monkeypatch.setattr(isolated_mcp_engram, "extract_session_insights", extract_session_insights)
    monkeypatch.setattr(isolated_mcp_engram, "append_daily_log", append_daily_log)

    first = json.loads(_run(mcp_server.wrap_up_session(
        summary="First closeout with idempotency key.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="same-closeout-key",
    )))
    second = json.loads(_run(mcp_server.wrap_up_session(
        summary="Retry should not write a second daily log.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="same-closeout-key",
    )))

    assert calls == {"extract": 1, "daily": 1}
    assert second["idempotent_replay"] is True
    assert second["operation"]["operation_id"] == first["operation"]["operation_id"]
    assert second["operation"]["status"] == "completed"


def test_wrap_up_session_records_running_stage_before_blocking_work(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    observed: dict[str, str] = {}

    def extract_session_insights(*args, **kwargs):
        op_files = list((isolated_mcp_engram.root / "operations" / "wrap_up_session").glob("*.json"))
        assert len(op_files) == 1
        state = json.loads(op_files[0].read_text(encoding="utf-8"))
        observed["status"] = state["status"]
        observed["current_stage"] = state["current_stage"]
        observed["stage_status"] = state["stages"]["extract_session_insights"]["status"]
        return {"saved_lessons": 0, "saved_decisions": 0, "results": []}

    monkeypatch.setattr(isolated_mcp_engram, "extract_session_insights", extract_session_insights)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Status should be persisted before stage body runs.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="running-stage-smoke",
    )))

    assert observed == {
        "status": "running",
        "current_stage": "extract_session_insights",
        "stage_status": "running",
    }
    assert payload["operation"]["status"] == "completed"


def test_wrap_up_session_partial_complete_when_optional_stage_fails(
    isolated_mcp_engram: Engram,
    monkeypatch,
) -> None:
    def append_daily_log(*args, **kwargs):
        raise RuntimeError("synthetic daily failure at /private/path")

    monkeypatch.setattr(isolated_mcp_engram, "append_daily_log", append_daily_log)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Daily log failure should not hide earlier committed stages.",
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="partial-closeout-smoke",
    )))

    assert payload["operation"]["status"] == "partial_complete"
    assert payload["operation"]["stages"]["append_daily_log"]["status"] == "error"
    assert payload["operation"]["committed"]["daily_log"] is False
    assert "/private/path" not in json.dumps(payload["operation"], ensure_ascii=False)


def test_completed_then_interrupted_wrap_up_fresh_process_handoff(
    isolated_mcp_engram: Engram,
    tmp_path: Path,
) -> None:
    project = tmp_path / "fresh-process-project"
    project.mkdir()

    json.loads(_run(mcp_server.wrap_up_session(
        summary=(
            "Goal: complete checkpoint cycle.\n"
            "Completed: verified completed checkpoint.\n"
            "Next: start follow-up cycle.\n"
        ),
        project_folder=str(project),
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="completed-checkpoint-closeout",
    )))
    completed = Engram(root=isolated_mcp_engram.root).build_project_resume_pack(
        project_folder=str(project),
    )
    assert completed["handoff"]["last_completed"] == [
        "verified completed checkpoint."
    ]
    assert completed["handoff"]["next_actions"] == ["start follow-up cycle."]

    json.loads(_run(mcp_server.wrap_up_session(
        summary=(
            "Goal: continue checkpoint cycle.\n"
            "Risks: blocked on synthetic external input.\n"
            "Next: resume after the input arrives.\n"
        ),
        project_folder=str(project),
        source_tool="codex",
        user_confirmed=True,
        idempotency_key="interrupted-checkpoint-closeout",
    )))
    interrupted = Engram(root=isolated_mcp_engram.root).build_project_resume_pack(
        project_folder=str(project),
    )

    assert interrupted["handoff"]["last_completed"] == []
    assert interrupted["handoff"]["next_actions"] == [
        "resume after the input arrives."
    ]
    assert interrupted["handoff"]["blocked_on"] == [
        "blocked on synthetic external input."
    ]
    assert interrupted["freshness"]["authoritative_source"] == "project_checkpoint"
