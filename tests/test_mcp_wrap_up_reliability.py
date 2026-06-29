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
