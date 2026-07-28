from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def isolated_runtime(tmp_path: Path, monkeypatch):
    store = tmp_path / "engram-store"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "off")
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")

    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)

    eng = Engram(root=store)
    (store / ".bootstrap_done").write_text("1", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_tracker", None)
    return store, project, eng


def test_runtime_entrypoints_return_parseable_payloads(isolated_runtime):
    store, project, _eng = isolated_runtime

    ctx = _run(mcp_server.get_user_context(level="standard"))
    assert isinstance(ctx, str)
    assert str(Path.home() / ".engram") not in ctx

    saved = json.loads(_run(mcp_server.save_agent_context(
        tool="codex",
        content="Implemented entrypoint smoke setup. Next: verify wrap-up.",
        project_folder=str(project),
    )))
    assert saved["tool"] == "codex"
    assert saved["session_id"]
    assert Path(saved["file"]).is_file()
    assert Path(saved["file"]).is_relative_to(store)

    brief = json.loads(_run(mcp_server.get_resume_brief(project_folder=str(project))))
    assert isinstance(brief, dict)
    assert "markdown" in brief
    assert str(store) not in brief["markdown"]


def test_runtime_wrap_up_default_is_lightweight(isolated_runtime, monkeypatch):
    _store, project, eng = isolated_runtime
    calls: list[str] = []

    def reconcile_memories():
        calls.append("mem")
        raise AssertionError("default closeout must not reconcile memories")

    def reconcile_ai_configs():
        calls.append("cfg")
        raise AssertionError("default closeout must not reconcile configs")

    monkeypatch.setattr(eng, "reconcile_memories", reconcile_memories)
    monkeypatch.setattr(eng, "reconcile_ai_configs", reconcile_ai_configs)

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Finished isolated runtime smoke.",
        source_tool="codex",
        project_folder=str(project),
        user_confirmed=True,
    )))

    assert calls == []
    assert payload["maintenance"]["reconcile_memories"]["status"] == "skipped"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "skipped"
    assert isinstance(payload["timing"]["total_ms"], int)


def test_runtime_wrap_up_explicit_reconcile_runs_only_when_requested(
    isolated_runtime,
    monkeypatch,
):
    _store, project, eng = isolated_runtime
    calls: list[tuple[str, dict]] = []

    def reconcile_memories(**kwargs):
        calls.append(("mem", kwargs))
        return {
            "imported": 0,
            "sources": [],
            "scope": {"mode": "project_exact"},
        }

    def reconcile_ai_configs(**kwargs):
        calls.append(("cfg", kwargs))
        return {
            "imported": 0,
            "sources": [],
            "scanned_files": 0,
            "budget_exhausted": False,
            "scope": {"mode": "project_exact"},
        }

    monkeypatch.setattr(
        eng,
        "reconcile_memories",
        reconcile_memories,
    )
    monkeypatch.setattr(
        eng,
        "reconcile_ai_configs",
        reconcile_ai_configs,
    )

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Owner-approved explicit reconcile smoke.",
        source_tool="codex",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=True,
    )))

    assert calls == [
        ("mem", {"project_folder": str(project)}),
        (
            "cfg",
            {
                "search_roots": [str(project)],
                "project_folder": str(project),
            },
        ),
    ]
    assert payload["maintenance"]["reconcile_scope"] == {
        "requested": "project",
        "effective": "project",
        "project_scoped": True,
    }
    assert payload["maintenance"]["reconcile_memories"]["status"] == "ok"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "ok"
