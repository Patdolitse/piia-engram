"""Tool labels and session ids never name files outside contexts/."""

import asyncio
import json

import pytest

from piia_engram import mcp_server
from piia_engram.contexts import _sanitize_tool_name
from piia_engram.core import Engram


@pytest.mark.parametrize("tool", ["..", ".", "..\\outside", "\\outside", "C:outside", "a/b", "a\\b"])
def test_context_write_stays_in_tool_directory(tmp_path, monkeypatch, tool):
    safe = _sanitize_tool_name(tool)
    # Fail before any file operation on buggy code, including drive/root labels.
    assert safe and safe not in (".", "..") and not any(c in safe for c in "/\\:")
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_track", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    result = json.loads(asyncio.run(mcp_server.save_agent_context(
        tool=tool, session_id="sample", content="Goal: test context containment")))
    from pathlib import Path

    path = Path(result["file"]).resolve()
    assert path.parent.parent == (eng.root / "contexts").resolve()
    assert path.is_file()


def test_context_reads_cannot_select_parent_directory(tmp_path):
    eng = Engram(root=tmp_path / "store")
    (eng.root / "outside.md").write_text("private marker", encoding="utf-8")
    assert eng.get_recent_context(tool="..") == []
    assert eng.list_agent_sessions(tool="..") == []


def test_session_digest_path_rejects_traversal(tmp_path):
    eng = Engram(root=tmp_path / "store")
    with pytest.raises(ValueError):
        eng._session_digest_path("codex", "../../outside")


@pytest.mark.parametrize("session_id", ["-session", "_session", "a..b", "session-name"])
def test_safe_legacy_session_ids_remain_writable_and_listable(tmp_path, session_id):
    from datetime import datetime
    from piia_engram.contexts import _sanitize_session_id_for_path

    assert _sanitize_session_id_for_path(session_id, datetime(2026, 1, 1)) == session_id
    eng = Engram(root=tmp_path / "store")
    tool_dir = eng.root / "contexts" / "codex"
    tool_dir.mkdir(parents=True)
    legacy = tool_dir / (session_id + ".md")
    legacy.write_text("# Legacy checkpoint\n\nGoal: verify context safety\n", encoding="utf-8")
    before = legacy.read_bytes()
    assert session_id in {r["session_id"] for r in eng.list_agent_sessions(tool="codex")}
    assert eng.get_recent_context(tool="codex")
    assert legacy.read_bytes() == before
    digest = {"schema": "session_digest.v1", "goal": "test"}
    (tool_dir / (session_id + ".digest.json")).write_text(json.dumps(digest), encoding="utf-8")
    assert eng.get_session_digest("codex", session_id) == digest
    result = eng.save_agent_context("codex", "Goal: verify context safety", session_id=session_id)
    assert result["session_id"] == session_id and result["appended"]
    assert legacy.read_bytes().startswith(before)


@pytest.mark.parametrize("session_id", ["-session", "a..b", "CON", "NUL.txt", "../escape", "a/b", "a\\b", "", ".", "x" * 129])
def test_every_sanitized_session_id_has_a_valid_contained_path(tmp_path, session_id):
    from datetime import datetime
    from piia_engram.contexts import _sanitize_session_id_for_path

    eng = Engram(root=tmp_path / "store")
    safe = _sanitize_session_id_for_path(session_id, datetime(2026, 1, 1))
    # Resolve before any write, including when the original label is a path.
    path = eng._context_session_path("codex", safe)
    assert path.resolve().parent == (eng.root / "contexts" / "codex").resolve()
    result = eng.save_agent_context("codex", "Goal: verify context safety", session_id=safe)
    assert result["session_id"] == safe
