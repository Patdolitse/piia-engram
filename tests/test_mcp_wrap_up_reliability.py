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
