"""A merge over MCP keeps relation links of a pinned third entry pointing at
the merged entry, and changes nothing else on it.

Relation links are link maintenance (as for manage_relation on a pinned
entry): the pinned entry's content, version and pin stay byte-for-byte the
same; only its related_ids swap the merged-away id for the surviving one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, pinning
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


def _row(eng: Engram, item_id: str) -> dict:
    _kind, row = eng._find_item_by_id(item_id)
    return dict(row or {})


def _pinned_third(eng: Engram, kind: str) -> dict:
    if kind == "lesson":
        return eng.add_lesson({"summary": "Pinned lesson about signing release tags", "domain": "release"})
    if kind == "decision":
        return eng.add_decision({"question": "Which key signs the release tags?", "choice": "the hardware key"})
    return eng.add_playbook({"title": "Pinned playbook for tag signing", "steps": [{"action": "sign"}]})


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_merge_only_remaps_related_ids_of_a_pinned_third_entry(eng, kind):
    a = eng.add_lesson({"summary": "Run the smoke tests before tagging", "domain": "release"})
    b = eng.add_lesson({"summary": "Database backups are verified every night", "domain": "ops"})
    c = _pinned_third(eng, kind)
    assert eng.link_knowledge(b["id"], c["id"]).get("error") is None
    assert pinning.pin(eng, c["id"]).get("status") in ("pinned", "already_pinned")
    before = _row(eng, c["id"])
    assert b["id"] in before["related_ids"]

    reply = json.loads(_run(mcp_server.merge_knowledge(
        a["id"], b["id"], primary_expected_version=1, secondary_expected_version=1)))
    assert reply.get("success") is True, reply

    after = _row(eng, c["id"])
    assert pinning.is_pinned(after)
    assert b["id"] not in after["related_ids"] and a["id"] in after["related_ids"]
    strip = lambda row: {k: v for k, v in row.items() if k != "related_ids"}  # noqa: E731
    assert strip(after) == strip(before)
