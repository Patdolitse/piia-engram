"""MCP knowledge writes must preview content before saving.

The MCP write surface is what client agents use to add durable Engram memory.
Before it mutates storage, it must show a clear title and detailed payload and
require explicit user confirmation.
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
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    return engram


def _run(coro):
    return asyncio.run(coro)


def test_add_lesson_requires_preview_confirmation_before_write(eng: Engram) -> None:
    out = _run(
        mcp_server.add_lesson(
            summary="Run focused tests before broad regressions",
            detail="Start with the boundary test, then widen to related suites.",
            domain="testing",
        )
    )

    payload = json.loads(out)
    assert payload["status"] == "confirmation_required"
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert payload["content_title"] == "Lesson: Run focused tests before broad regressions"
    assert "Start with the boundary test" in payload["content_detail"]
    assert eng.get_lessons(limit=None, _update_access=False) == []

    confirmed = _run(
        mcp_server.add_lesson(
            summary="Run focused tests before broad regressions",
            detail="Start with the boundary test, then widen to related suites.",
            domain="testing",
            user_confirmed=True,
        )
    )

    assert "Run focused tests before broad regressions" in confirmed
    assert len(eng.get_lessons(limit=None, _update_access=False)) == 1


def test_string_false_does_not_count_as_write_confirmation(eng: Engram) -> None:
    out = _run(
        mcp_server.add_lesson(
            summary="String false must preview only",
            user_confirmed="false",
        )
    )

    payload = json.loads(out)
    assert payload["status"] == "confirmation_required"
    assert payload["changed"] is False
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_string_true_is_accepted_as_explicit_write_confirmation(eng: Engram) -> None:
    out = _run(
        mcp_server.memory_store(
            "lesson",
            json.dumps({"summary": "String true can confirm writes"}),
            user_confirmed="true",
        )
    )

    assert "String true can confirm writes" in out
    lessons = eng.get_lessons(limit=None, _update_access=False)
    assert len(lessons) == 1
    assert lessons[0]["summary"] == "String true can confirm writes"


def test_memory_store_decision_requires_confirmation_preview(eng: Engram) -> None:
    content = {
        "question": "How should MCP memory writes be approved?",
        "choice": "Preview title and detail first, then require user confirmation.",
        "reasoning": "This prevents accidental or unclear long-term memory writes.",
    }

    out = _run(mcp_server.memory_store("decision", json.dumps(content)))
    payload = json.loads(out)

    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Decision: How should MCP memory writes be approved?"
    assert "Preview title and detail first" in payload["content_detail"]
    assert eng.get_decisions(limit=None, _update_access=False) == []


def test_memory_store_batch_preview_lists_all_items_without_writing(eng: Engram) -> None:
    items = [
        {"summary": "First candidate memory", "domain": "testing"},
        {"summary": "Second candidate memory", "domain": "testing"},
    ]

    out = _run(mcp_server.memory_store(kind="lesson", items_json=json.dumps(items)))
    payload = json.loads(out)

    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Batch lesson memory write: 2 items"
    assert "First candidate memory" in payload["content_detail"]
    assert "Second candidate memory" in payload["content_detail"]
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_ingest_notes_requires_confirmation_preview(eng: Engram) -> None:
    notes = "learned that pytest fixtures simplify repeatable integration testing workflows"

    out = _run(
        mcp_server.ingest_notes(
            notes,
            source_tool="codex",
            domain="testing",
        )
    )
    payload = json.loads(out)

    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Ingest notes memory extraction"
    assert notes in payload["content_detail"]
    assert eng.get_lessons(limit=None, _update_access=False) == []

    confirmed = _run(
        mcp_server.ingest_notes(
            notes,
            source_tool="codex",
            domain="testing",
            user_confirmed=True,
        )
    )
    assert json.loads(confirmed)["saved_lessons"] >= 1
    assert eng.get_lessons(limit=None, _update_access=False)


def test_extract_session_insights_requires_confirmation_preview(eng: Engram) -> None:
    summary = (
        "We decided to use pytest for release checks because it gives reliable "
        "feedback before publishing."
    )

    out = _run(
        mcp_server.extract_session_insights(
            summary,
            source_tool="codex",
        )
    )
    payload = json.loads(out)

    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Extract session insights memory write"
    assert "pytest for release checks" in payload["content_detail"]
    assert eng.get_decisions(limit=None, _update_access=False) == []

    confirmed = _run(
        mcp_server.extract_session_insights(
            summary,
            source_tool="codex",
            user_confirmed=True,
        )
    )
    assert json.loads(confirmed)["saved_decisions"] >= 1
    assert eng.get_decisions(limit=None, _update_access=False)


def test_wrap_up_session_requires_confirmation_preview(eng: Engram) -> None:
    summary = (
        "We decided to run pytest before release because it gives reliable "
        "feedback before publishing."
    )
    project_a = str(eng.root / "project-a")
    project_b = str(eng.root / "project-b")

    out = _run(
        mcp_server.wrap_up_session(
            summary=summary,
            source_tool="codex",
            project_folder=project_a,
            project_title="Test Project",
            tech_stack="python,pytest",
            known_issues="none",
        )
    )
    payload = json.loads(out)

    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Wrap up session memory write"
    assert "pytest before release" in payload["content_detail"]
    assert eng.get_lessons(limit=None, _update_access=False) == []
    assert eng.get_decisions(limit=None, _update_access=False) == []
    assert eng.get_playbooks(limit=None) == []

    confirmed = _run(
        mcp_server.wrap_up_session(
            summary=summary,
            source_tool="codex",
            project_folder=project_a,
            project_title="Test Project",
            tech_stack="python,pytest",
            known_issues="none",
            user_confirmed=True,
        )
    )
    parsed = json.loads(confirmed)
    assert "insights" in parsed
    assert "confirmation_required" not in parsed
    assert parsed["insights"]["saved_decisions"] >= 1

    project_decisions = eng.get_decisions(
        limit=None, project_folder=project_a, _update_access=False
    )
    assert project_decisions
    assert eng.get_decisions(limit=None, project_folder=project_b, _update_access=False) == []
    assert eng.get_decisions(limit=None, _update_access=False) == []
    stored = project_decisions[0]
    assert stored["project_id"]
    assert "project_folder" not in stored
    assert "source_project_folder" not in stored


def test_add_playbook_requires_confirmation_preview(eng: Engram) -> None:
    out = _run(
        mcp_server.add_playbook(
            title="Release check",
            triggers="release,verify",
            steps_json='["Run tests", "Check package"]',
            description="Small release verification flow.",
        )
    )

    payload = json.loads(out)
    assert payload["status"] == "confirmation_required"
    assert payload["content_title"] == "Playbook: Release check"
    assert "Small release verification flow" in payload["content_detail"]
    assert eng.get_playbooks(limit=None) == []
