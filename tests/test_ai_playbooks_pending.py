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


# ---------------------------------------------------------------------------
# an AI's rewrite of an approved playbook is a proposal; the approved one stays
# ---------------------------------------------------------------------------


def test_mcp_update_of_an_approved_playbook_is_a_proposal(eng, tmp_path):
    from piia_engram import review_cli

    approved = eng.add_playbook({"title": "Ship the mobile build", "steps": [{"action": "old step one"},
                                                                         {"action": "old step two"}]})
    before = eng._read_playbook_by_id(approved["id"])
    reply = json.loads(_run(mcp_server.manage_playbook(
        "update", approved["id"], steps_json=json.dumps([{"action": "new step"}]), expected_version=1)))
    assert reply["status"] == "pending" and reply["pending_supersedes"] == approved["id"]
    after = eng._read_playbook_by_id(approved["id"])
    assert after["tier"] == "verified" and after["steps"] == before["steps"] and after["version"] == 1
    plan = json.loads(_run(mcp_server.playbook_execution(action="prepare", playbook_id=approved["id"])))
    assert [s["action"] for s in plan["execution_plan"]] == ["old step one", "old step two"]
    proposal = eng._read_playbook_by_id(reply["id"])
    assert proposal["tier"] == "staging" and [s["action"] for s in proposal["steps"]] == ["new step"]

    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": reply["id"], "mark": "approve"}]), encoding="utf-8")
    assert review_cli.run_apply([str(marks), "--operator", "owner", "--yes"]) == 0
    assert eng._read_playbook_by_id(approved["id"])["status"] != "active"
    plan = json.loads(_run(mcp_server.playbook_execution(action="prepare", playbook_id=reply["id"])))
    assert [s["action"] for s in plan["execution_plan"]] == ["new step"]


def test_mcp_status_only_update_stays_direct_and_mixed_updates_are_refused(eng):
    approved = eng.add_playbook({"title": "Rotate the CDN keys", "steps": [{"action": "rotate"}]})
    mixed = json.loads(_run(mcp_server.manage_playbook("update", approved["id"], title="Rotate keys",
                                                       status="outdated", expected_version=1)))
    assert mixed["error"] == "mixed_update"
    assert eng._read_playbook_by_id(approved["id"])["status"] == "active"


# ---------------------------------------------------------------------------
# the pending-playbook cap applies to every AI entry point
# ---------------------------------------------------------------------------


def _playbook_files(eng: Engram) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (eng.root / "playbooks").glob("*.json")}


def test_ai_playbook_queue_cap_in_default_mode(eng, monkeypatch):
    monkeypatch.setenv("ENGRAM_PLAYBOOK_QUEUE_MAX", "1")
    first = json.loads(_run(mcp_server.add_playbook(title="Water the office plants", triggers="plants",
                                                   steps_json=json.dumps([{"action": "branch"}]),
                                                   user_confirmed=True)))
    assert first["status"] == "pending"
    before = _playbook_files(eng)

    added = json.loads(_run(mcp_server.add_playbook(title="Archive old invoices", triggers="invoices",
                                                   steps_json=json.dumps([{"action": "publish"}]),
                                                   user_confirmed=True)))
    assert added["status"] == "queue_full"
    stored = json.loads(_run(mcp_server.memory_store(kind="playbook", content_json=json.dumps(
        {"title": "Calibrate the label printer", "steps": [{"action": "calibrate"}]}), user_confirmed=True)))
    assert stored["status"] == "queue_full"
    wrapped = json.loads(_run(mcp_server.wrap_up_session(
        summary="Steps: 1. first build the package, 2. then run the tests, 3. then upload the wheel, "
                "4. finally tag the release.", user_confirmed=True)))
    assert wrapped["playbook_draft"]["status"] == "queue_full"
    assert _playbook_files(eng) == before
