"""Data-label maturity metadata for knowledge entries.

The labeling block is system-derived, not caller-certified. It gives recall and
review surfaces a small, explainable signal about whether a memory is raw,
partial, or mature without letting MCP clients smuggle trust claims.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
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
    if getattr(mcp_server, "_ToolCallTracker", None) is not None:
        monkeypatch.setattr(
            mcp_server, "_tracker", mcp_server._ToolCallTracker(), raising=False
        )

    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def test_agent_lesson_gets_partial_unreviewed_labeling(tmp_path: Path):
    engram = Engram(root=tmp_path)
    lesson = engram.add_lesson(
        {
            "summary": "prefer narrow tests before changing shared memory logic",
            "domain": "testing",
            "source_tool": "codex",
            "provenance": {"source_agent": "codex", "run_id": "run-1"},
        }
    )

    assert lesson["labeling"]["source_kind"] == "agent"
    assert lesson["labeling"]["annotation_quality"] == "partial"
    assert lesson["labeling"]["validation_state"] == "unreviewed"
    assert "has_source_agent" in lesson["labeling"]["signals"]
    assert "has_domain" in lesson["labeling"]["signals"]


def test_validated_lesson_gets_mature_labeling(tmp_path: Path):
    engram = Engram(root=tmp_path)
    lesson = engram.add_lesson(
        {
            "summary": "validated release checklist lesson",
            "domain": "release",
            "source_tool": "codex",
            "provenance": {
                "source_agent": "codex",
                "run_id": "run-2",
                "last_validated_at": "2026-06-15T10:00:00Z",
            },
        }
    )

    assert lesson["labeling"]["source_kind"] == "agent"
    assert lesson["labeling"]["annotation_quality"] == "mature"
    assert lesson["labeling"]["validation_state"] == "validated"
    assert "has_last_validated_at" in lesson["labeling"]["signals"]


def test_high_risk_staging_lesson_is_labeled_needs_review(tmp_path: Path):
    engram = Engram(root=tmp_path)
    lesson = engram.add_lesson(
        {
            "summary": "rotate api_key with a powershell command",
            "domain": "ops",
            "source_tool": "codex",
            "provenance": {
                "source_agent": "codex",
                "run_id": "run-3",
                "last_validated_at": "2026-06-15T10:00:00Z",
            },
        }
    )

    assert lesson["tier"] == "staging"
    assert lesson["labeling"]["validation_state"] == "needs_review"
    assert lesson["labeling"]["annotation_quality"] == "partial"
    assert "needs_owner_review" in lesson["labeling"]["signals"]
    assert "high_risk" in lesson["labeling"]["signals"]


def test_memory_store_cannot_smuggle_mature_labeling(eng: Engram):
    payload = {
        "summary": "a plain tool note",
        "domain": "workflow",
        "source_tool": "codex",
        "labeling": {
            "source_kind": "human",
            "annotation_quality": "mature",
            "validation_state": "validated",
            "signals": ["caller_certified"],
        },
    }

    _run(
        mcp_server.memory_store(
            kind="lesson",
            content_json=json.dumps(payload),
            source_tool="codex",
        )
    )

    lesson = eng.get_lessons()[0]
    assert lesson["labeling"]["source_kind"] == "agent"
    assert lesson["labeling"]["annotation_quality"] == "partial"
    assert lesson["labeling"]["validation_state"] == "unreviewed"
    assert "caller_certified" not in lesson["labeling"]["signals"]


def test_review_knowledge_marks_validated_and_refreshes_labeling(tmp_path: Path):
    engram = Engram(root=tmp_path)
    lesson = engram.add_lesson({
        "summary": "reviewed memory should become validated",
        "domain": "review",
        "source_tool": "codex",
        "provenance": {"source_agent": "codex", "run_id": "run-4"},
    })

    reviewed = engram.review_knowledge(lesson["id"])

    assert reviewed["labeling"]["validation_state"] == "validated"
    assert "has_last_validated_at" in reviewed["labeling"]["signals"]
    assert reviewed["provenance"]["source_agent"] == "owner"
    assert "last_validated_at" in reviewed["provenance"]


def test_promote_knowledge_marks_validated_and_refreshes_labeling(tmp_path: Path):
    engram = Engram(root=tmp_path)
    lesson = engram.add_lesson({
        "summary": "promotion validates staging memory",
        "domain": "review",
        "source_tool": "codex",
        "tier": "staging",
    })

    result = engram.promote_knowledge(lesson["id"])
    stored = engram.get_lessons(limit=None, _update_access=False)[0]

    assert result["status"] == "promoted"
    assert stored["tier"] == "verified"
    assert stored["labeling"]["validation_state"] == "validated"
    assert "has_last_validated_at" in stored["labeling"]["signals"]
