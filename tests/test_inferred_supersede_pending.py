"""A decision an AI adds that would replace a reviewed decision (same question,
different choice) waits for the Owner, in every approval mode.

Over MCP the new decision is a pending proposal with pending_supersedes=<old
id>; no supersedes edge is written and the reviewed decision stays in use
until the Owner approves the replacement. Local writes keep the automatic
decision-thread edge.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, review_cli
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore


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


def _row(eng: Engram, item_id: str) -> dict:
    return dict(eng._find_item_by_id(item_id)[1] or {})


def _edges(eng: Engram) -> list[dict]:
    return [e for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]


@pytest.mark.parametrize("mode", ["default", "strict"])
def test_mcp_inferred_replacement_is_a_pending_proposal(eng, monkeypatch, tmp_path, mode):
    old = eng.add_decision({"question": "Which formatter does the repo use?", "choice": "black"})
    assert _row(eng, old["id"])["tier"] == "verified"
    if mode == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    reply = _run(mcp_server.add_decision(question="Which formatter does the repo use?", choice="ruff format",
                                         reasoning="faster", user_confirmed=True))
    rows = [r for r in eng.get_decisions(limit=None, _update_access=False) if r.get("choice") == "ruff format"]
    assert len(rows) == 1, reply
    new = rows[0]
    assert new["tier"] == "staging" and new.get("pending_supersedes") == old["id"]
    assert _edges(eng) == []
    assert _row(eng, old["id"])["tier"] == "verified" and _row(eng, old["id"])["status"] == "active"

    # the Owner's local review approves it: the edge is written then
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": new["id"], "mark": "approve"}]), encoding="utf-8")
    assert review_cli.run_apply([str(marks), "--operator", "owner", "--yes"]) == 0
    assert {(e["src"], e["dst"]) for e in _edges(eng)} == {(new["id"], old["id"])}


def test_local_inferred_replacement_keeps_the_automatic_edge(eng):
    old = eng.add_decision({"question": "Which test runner does the repo use?", "choice": "unittest"})
    new = eng.add_decision({"question": "Which test runner does the repo use?", "choice": "pytest"})
    assert _row(eng, new["id"])["tier"] == "verified"
    assert {(e["src"], e["dst"]) for e in _edges(eng)} == {(new["id"], old["id"])}


def test_mcp_replacement_of_a_pending_decision_is_unchanged(eng):
    pending = eng.add_decision({"question": "Which linter does the repo use?", "choice": "flake8",
                                "tier": "staging"})
    _run(mcp_server.add_decision(question="Which linter does the repo use?", choice="ruff",
                                 user_confirmed=True))
    rows = [r for r in eng.get_decisions(limit=None, _update_access=False) if r.get("choice") == "ruff"]
    assert len(rows) == 1
    assert _row(eng, pending["id"])["tier"] == "staging"
