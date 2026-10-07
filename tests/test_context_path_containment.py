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
