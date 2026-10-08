"""A pending playbook never runs, in any approval mode.

* playbook_execution (prepare, update_step) refuses a staging playbook with
  ``not_approved`` (strict keeps ``pending_not_executable``) and writes nothing;
* once the Owner approves it, it runs;
* get_playbooks still lists it, marked ``pending_untrusted`` / ``eligibility``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging


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


def _store(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for sub in ("knowledge", "playbooks", "executions") if (root / sub).exists()
        for p in sorted((root / sub).rglob("*")) if p.is_file()
    }


def _ai_playbook() -> str:
    reply = json.loads(_run(mcp_server.add_playbook(
        title="Publish the release notes", triggers="release",
        steps_json=json.dumps([{"action": "draft"}, {"action": "publish"}]), user_confirmed=True)))
    assert reply["status"] == "pending"
    return reply["id"]


def test_a_pending_playbook_is_not_executed_until_approved(eng):
    pb_id = _ai_playbook()
    before = _store(eng.root)
    refused = json.loads(_run(mcp_server.playbook_execution(action="prepare", playbook_id=pb_id)))
    assert refused["status"] == "not_approved" and "engram review" in refused["message"]
    step = json.loads(_run(mcp_server.playbook_execution(action="update_step", playbook_id=pb_id,
                                                          step_order=1, step_status="completed")))
    assert step["status"] == "not_approved"
    assert _store(eng.root) == before

    approved = batch_review_staging(eng, [{"id": pb_id, "action": "approve"}], dry_run=False, confirm=True,
                                    owner_cli=True)
    assert approved["counts"]["applied"] == 1
    plan = json.loads(_run(mcp_server.playbook_execution(action="prepare", playbook_id=pb_id)))
    assert plan.get("status") != "not_approved" and plan.get("execution_plan")


def test_lists_mark_pending_playbooks(eng):
    pb_id = _ai_playbook()
    local = eng.add_playbook({"title": "A reviewed local playbook", "steps": [{"action": "x"}]})
    listed = {item["id"]: item for item in json.loads(_run(mcp_server.get_playbooks(limit=50)))}
    assert listed[pb_id]["pending_untrusted"] is True and listed[pb_id]["eligibility"] == "pending"
    assert not listed[local["id"]].get("pending_untrusted")
    one = json.loads(_run(mcp_server.get_playbooks(playbook_id=pb_id)))
    assert one["pending_untrusted"] is True and one["tier"] == "staging"
    managed = json.loads(_run(mcp_server.get_playbooks(mode="management", limit=50)))
    assert {item["id"]: item.get("pending_untrusted") for item in managed["items"]}[pb_id] is True


def test_strict_keeps_its_refusal(eng, monkeypatch):
    pb_id = _ai_playbook()
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    refused = json.loads(_run(mcp_server.playbook_execution(action="prepare", playbook_id=pb_id)))
    assert refused["status"] == "pending_not_executable"
