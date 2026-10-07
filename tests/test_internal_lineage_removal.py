"""Public relation removal cannot undo internally generated version lineage."""

import asyncio
import json

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


@pytest.mark.parametrize("route", ["core", "mcp"])
def test_supersedes_removal_refuses_without_writes(tmp_path, monkeypatch, route):
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": "Which deployment target?", "choice": "one", "tier": "verified"})
    new = eng.add_decision({"question": "Which deployment target?", "choice": "two", "tier": "verified"})
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_track", lambda *a, **k: None)
    before = {p.name: p.read_bytes() for p in eng._knowledge_dir.iterdir() if p.is_file()}
    if route == "mcp":
        result = json.loads(asyncio.run(mcp_server.manage_relation("unlink", new["id"], old["id"], rel="supersedes")))
    else:
        result = eng.remove_relation(new["id"], "supersedes", old["id"])
    assert result.get("reason") == "supersedes_is_internal", result
    assert result["removed"] is False
    assert {p.name: p.read_bytes() for p in eng._knowledge_dir.iterdir() if p.is_file()} == before
    assert old["id"] not in eng.get_decision_thread(new["id"])["active_ids"]
