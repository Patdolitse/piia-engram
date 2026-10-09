"""Starting the MCP server and reading memory never imports other AI tools' files.

Covers the server start (``main()`` with the stdio transport stubbed out), the
store open, the first tool calls (get_user_context, get_resume_brief,
search_knowledge), cold start on an empty store, the SessionStart hook and
wrap_up_session(run_reconcile=True). Importing other AI tools' memories is
only done by the explicit ``engram import-memories`` command.

Every test points HOME / USERPROFILE at a temporary directory that holds a
fake set of other AI tools' memory and rule files (conftest
``other_ai_tools_home``).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram

# Knowledge, identity, playbooks and project snapshots: must stay byte-identical.
PROTECTED_DIRS = ("knowledge", "identity", "playbooks", "projects")
LOCK_NAME = ".engram-write.lock"


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _protected(snapshot: dict[str, str]) -> dict[str, str]:
    return {
        rel: digest for rel, digest in snapshot.items()
        if rel.split("/")[0] in PROTECTED_DIRS and not rel.endswith(LOCK_NAME)
    }


def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))


# Files a server start, a few reads and a clean exit may still change, and why:
#   audit.log                   local audit trail (reads are logged; on by default)
#   session_state.json          clean/unclean exit marker, stamped at each store open
#   .migrated_version           once per installed version (config notice only)
#   beta_events.jsonl           local usage event log (cold_start event)
#   contexts/...                the session log saved at exit
#   file_safety_ledger.jsonl, backups/file_safety/...session_state.json...
#                               the file-safety copy taken before session_state.json
#                               is rewritten
#   .engram-write.lock          empty lock files
def _is_runtime_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if rel in {"audit.log", "session_state.json", ".migrated_version",
               "beta_events.jsonl", "file_safety_ledger.jsonl"}:
        return True
    if rel.startswith("contexts/"):
        return True
    if rel.startswith("backups/file_safety/") and name.startswith("session_state.json."):
        return True
    return name == LOCK_NAME


def _seed_store(root: Path) -> None:
    seed = Engram(root=root)
    seed.update_profile({"role": "developer"})
    seed.add_lesson(
        "Pin test fixtures to a temporary store so suites never share state",
        domain="testing",
        source_tool="cli",
    )
    seed.add_decision(
        "Which test runner do we use for the service?",
        choice="pytest",
        reasoning="Fixtures and parametrize cover every case we have.",
        source_tool="cli",
    )
    seed.add_lesson(
        "Review queue sample: prefer explicit imports over magic startup work",
        domain="testing",
        source_tool="cli",
        tier="staging",
    )


def _start_server(monkeypatch) -> None:
    """What ``piia-engram-mcp`` does at process start, with the transport stubbed."""
    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram, error = mcp_server._init_engram()  # the module-level store open
    assert error is None
    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setattr(mcp_server, "_init_error", None)
    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: None)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: None)
    mcp_server.main()
    # The stub transport returns immediately; keep this synthetic session open
    # for the tool calls below, and restore the original flag during teardown.
    monkeypatch.setattr(mcp_server, "_shutting_down", False)
    # Wait for anything the start scheduled in the background.
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name.startswith("engram-startup"):
            thread.join(timeout=10)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch, other_ai_tools_home) -> Path:
    monkeypatch.setattr(mcp_server, "_shutting_down", False)
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")  # the real default; reads are audited
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    return root


@pytest.mark.parametrize("startup_sync", ["", "background", "eager", "1", "off"])
def test_server_start_and_reads_leave_knowledge_and_identity_untouched(
    store, monkeypatch, tmp_path, startup_sync,
):
    if startup_sync:
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", startup_sync)
    _seed_store(store)
    before = _snapshot(store)

    _start_server(monkeypatch)
    project = tmp_path / "work"
    project.mkdir()
    _run(mcp_server.get_user_context(project_folder=str(project)))
    _run(mcp_server.get_user_context(level="full"))
    _run(mcp_server.get_resume_brief(project_folder=str(project)))
    _run(mcp_server.search_knowledge(query="linter"))
    _run(mcp_server.search_knowledge(query="test fixtures"))
    mcp_server._engram_clean_shutdown()

    after = _snapshot(store)
    assert _protected(after) == _protected(before)
    changed = _changed(before, after)
    unexpected = [rel for rel in changed if not _is_runtime_file(rel)]
    assert unexpected == [], f"unexpected store writes: {unexpected}"
    assert not (store / "import_receipts").exists()
    assert not (store / ".bootstrap_done").exists()


def test_cold_start_on_an_empty_store_imports_nothing(store, monkeypatch):
    _start_server(monkeypatch)

    context = _run(mcp_server.get_user_context())
    _run(mcp_server.get_user_context(level="full"))
    brief = _run(mcp_server.get_resume_brief())

    reader = Engram(root=store, read_only=True)
    assert reader.get_lessons(limit=None, _update_access=False) == []
    assert reader.get_decisions(limit=None, _update_access=False) == []
    assert "language" not in reader.get_profile()
    assert not (store / ".bootstrap_done").exists()
    # A new user is told how to bring in other tools' memories themselves.
    assert "engram import-memories" in context
    assert "首次连接自动导入" not in context
    assert "首次连接自动导入" not in brief


def test_session_start_hook_imports_nothing(store, monkeypatch, tmp_path, capsys):
    from piia_engram.hooks import auto_inject_resume_brief as hook

    monkeypatch.delenv("CLAUDE_INVOKED_BY", raising=False)
    monkeypatch.setattr(sys, "argv", ["auto_inject_resume_brief"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    hook.main()

    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1])["continue"] is True
    reader = Engram(root=store, read_only=True)
    assert reader.get_lessons(limit=None, _update_access=False) == []
    assert not (store / ".bootstrap_done").exists()


def test_full_cold_start_and_quick_context_import_nothing(store):
    engram = Engram(root=store)
    engram.update_profile({"role": "developer"})

    context = engram.generate_context()  # default level is "full"
    engram.refresh_quick_context(level="full")

    lessons = engram.get_lessons(limit=None, _update_access=False)
    assert lessons == []
    assert "auto_sync" not in context


def test_wrap_up_with_run_reconcile_no_longer_imports(store, monkeypatch, tmp_path):
    _start_server(monkeypatch)
    project = tmp_path / "work"
    project.mkdir()

    payload = json.loads(_run(mcp_server.wrap_up_session(
        summary="Short session: read some files.",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=True,
    )))

    maintenance = payload["maintenance"]
    for stage in ("reconcile_memories", "reconcile_ai_configs"):
        assert maintenance[stage]["status"] == "skipped"
        assert maintenance[stage]["reason"] == "explicit_import_only"
    assert "engram import-memories" in json.dumps(payload, ensure_ascii=False)
    reader = Engram(root=store, read_only=True)
    sources = {row.get("source_tool") for row in reader.get_lessons(limit=None, _update_access=False)}
    assert not sources & {"auto_reconcile", "config_scan", "engram_bootstrap"}


def test_server_start_never_calls_the_import_engine(store, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(Engram, "reconcile_memories", lambda self, **kw: calls.append("mem") or {})
    monkeypatch.setattr(Engram, "reconcile_ai_configs", lambda self, **kw: calls.append("cfg") or {})
    for value in ("", "background", "eager", "sync", "1", "off", "not-a-mode"):
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", value)
        _start_server(monkeypatch)
    assert calls == []
