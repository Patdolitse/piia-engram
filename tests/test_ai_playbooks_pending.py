"""Playbooks an AI writes over MCP are proposals in every approval mode.

* add_playbook / memory_store(kind="playbook") over MCP store a pending row
  (tier staging) and the reply says the Owner approves it with engram review;
* a pending playbook stays out of automatic recall;
* local Owner actions (engram playbook install, seeds) and existing verified
  playbooks are unchanged.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, recall_policy
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _rows(eng: Engram) -> dict[str, dict]:
    out = {}
    for path in (eng.root / "playbooks").glob("*.json"):
        if not path.name.startswith("_"):
            row = eng._read_playbook_by_id(path.stem)
            out[row["title"]] = row
    return out


def test_mcp_playbooks_are_pending_in_default_mode(eng):
    reply = json.loads(_run(mcp_server.add_playbook(
        title="Release the docs site", triggers="docs", steps_json=json.dumps([{"action": "build"}]),
        user_confirmed=True)))
    assert reply["status"] == "pending" and "engram review" in reply["message"]
    text = _run(mcp_server.memory_store(kind="playbook", content_json=json.dumps(
        {"title": "Rotate the signing key", "steps": [{"action": "rotate"}]}), user_confirmed=True))
    assert "engram review" in text
    rows = _rows(eng)
    for title in ("Release the docs site", "Rotate the signing key"):
        assert rows[title]["tier"] == "staging" and rows[title]["approval_status"] == "pending"
        assert recall_policy.classify(rows[title]).state == recall_policy.PENDING
    recent = eng.get_recent_playbooks(limit=10)
    assert not [pb for pb in recent if pb["title"] in ("Release the docs site", "Rotate the signing key")]


def test_local_owner_playbooks_stay_verified(eng):
    local = eng.add_playbook({"title": "A playbook the Owner writes locally", "steps": [{"action": "x"}]})
    assert eng._read_playbook_by_id(local["id"])["tier"] == "verified"
    from piia_engram import write_provenance

    with write_provenance.origin_scope(write_provenance.ORIGIN_CLI):  # e.g. engram playbook install
        cli = eng.add_playbook({"title": "A playbook installed with the local command",
                                "steps": [{"action": "y"}]})
    assert eng._read_playbook_by_id(cli["id"])["tier"] == "verified"


def test_existing_verified_playbooks_are_unaffected(eng):
    local = eng.add_playbook({"title": "An existing verified playbook", "steps": [{"action": "x"}]})
    _run(mcp_server.add_playbook(title="A new AI playbook next to it", triggers="x",
                                 steps_json=json.dumps([{"action": "y"}]), user_confirmed=True))
    assert eng._read_playbook_by_id(local["id"])["tier"] == "verified"
    assert local["id"] in {pb["id"] for pb in eng.get_recent_playbooks(limit=10)}


def test_server_instructions_say_ai_playbooks_wait_for_review(monkeypatch):
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    assert "engram review" in mcp_server._DEFAULT_SERVER_INSTRUCTIONS
    assert "playbook" in mcp_server._DEFAULT_SERVER_INSTRUCTIONS.lower()
