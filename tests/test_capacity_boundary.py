"""Caller-facing boundary of the capacity layer: status values and system fields."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import capacity, mcp_server
from piia_engram.core import Engram
from piia_engram.storage import strip_untrusted_trust_fields

CALLER_TIME = "2000-01-01T00:00:00Z"


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    old_session = mcp_server._session
    try:
        old_session._stop_event.set()
        thread = getattr(old_session, "_heartbeat_thread", None)
        if thread is not None:
            thread.join(timeout=1.0)
    except Exception:
        pass
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_track_count", 0, raising=False)
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _lessons_bytes(eng: Engram) -> bytes:
    return (eng.root / "knowledge" / "lessons.json").read_bytes()


@pytest.mark.parametrize("status", ["done", "superseded", "archived", ""])
def test_update_lesson_rejects_a_status_outside_the_contract(eng: Engram, status: str):
    lesson_id = eng.add_lesson({"summary": "a lesson whose status is updated"})["id"]
    before = _lessons_bytes(eng)
    result = eng.update_knowledge(lesson_id, {"status": status})
    assert result["error"] == "invalid_status"
    assert result["item_id"] == lesson_id
    assert _lessons_bytes(eng) == before


def test_update_decision_rejects_a_status_outside_the_contract(eng: Engram):
    decision_id = eng.add_decision(
        {"question": "Which queue shape?", "choice": "A bounded queue", "reasoning": "Predictable size."}
    )["id"]
    result = eng.update_decision(decision_id, {"status": "superseded"})
    assert result["error"] == "invalid_status"


@pytest.mark.parametrize("status", sorted(capacity.UPDATABLE_STATUSES))
def test_update_lesson_accepts_every_contract_status(eng: Engram, status: str):
    lesson_id = eng.add_lesson({"summary": "a lesson whose status is updated"})["id"]
    result = eng.update_knowledge(lesson_id, {"status": status})
    assert "error" not in result
    assert result["status"] == status


def test_strip_removes_every_system_field():
    extra = ("snapshot_of", "overflow_archived_at", "overflow_archive_reason")
    payload = {"summary": "keep me", **{name: CALLER_TIME for name in (*capacity.SYSTEM_FIELDS, *extra)}}
    strip_untrusted_trust_fields(payload)
    assert payload == {"summary": "keep me"}


def test_memory_store_ignores_caller_supplied_system_fields(eng: Engram):
    payload = {
        "summary": "a plain tool note with enough words",
        "domain": "workflow",
        "ingested_at": CALLER_TIME,
        "queued_at": CALLER_TIME,
        "retired_at": CALLER_TIME,
        "snapshot_of": "L-forged",
        "overflow_archive_reason": "removed",
    }
    asyncio.run(
        mcp_server.memory_store(
            kind="lesson", content_json=json.dumps(payload), source_tool="codex", user_confirmed=True
        )
    )
    stored = json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert len(stored) == 1
    row = stored[0]
    assert row.get("ingested_at") not in (None, CALLER_TIME)
    for name in ("queued_at", "retired_at", "snapshot_of", "overflow_archive_reason"):
        assert name not in row
