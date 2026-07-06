"""Phase 1 wiring tests — provenance on the write path + opt-in freshness on read.

Covers the Provenance & Freshness Contract v1 follow-ups A (write-path normalize)
and B (recall-path opt-in annotation), proving both are strictly ADDITIVE:
old call shapes and default outputs are unchanged.
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
    # Hermetic session/tracking isolation: these tests drive real mcp_server
    # tools, which increment the *global* session call counter + telemetry
    # tracker. Left unreset, that shared counter shifts the checkpoint-boundary
    # alignment for later tests (e.g. the write-gate WriterSpy sweep), so we give
    # each test a fresh, heartbeat-free session and reset the trackers. monkeypatch
    # restores the originals afterwards, so this test file contributes nothing to
    # the global counter.
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


# ---------------------------------------------------------------------------
# Follow-up A: write-path provenance normalization
# ---------------------------------------------------------------------------


class TestWritePathProvenance:
    def test_add_lesson_without_provenance_is_unchanged(self, eng: Engram):
        """Old call shape: none of the v1 provenance fields are injected.

        ``core._ensure_fields`` already stamps a baseline ``provenance`` dict
        (source_tool/created_at/entry_type); the additive contract is that the
        v1 fields (source_agent/run_id/last_validated_at) appear ONLY when the
        caller supplies them.
        """
        _run(mcp_server.add_lesson(summary="a plain durable lesson", user_confirmed=True))
        lessons = eng.get_lessons()
        found = [l for l in lessons if l["summary"] == "a plain durable lesson"]
        assert found
        prov = found[0].get("provenance", {})
        assert "source_agent" not in prov
        assert "run_id" not in prov
        assert "last_validated_at" not in prov

    def test_add_lesson_stores_normalized_provenance(self, eng: Engram):
        _run(mcp_server.add_lesson(
            summary="lesson with provenance",
            source_agent="claude_code",
            run_id="wf-123",
            last_validated_at="2026-05-01T10:00:00Z",
            user_confirmed=True,
        ))
        found = [l for l in eng.get_lessons() if l["summary"] == "lesson with provenance"]
        assert found
        prov = found[0]["provenance"]
        assert prov["source_agent"] == "claude_code"
        assert prov["run_id"] == "wf-123"
        # normalized to ISO UTC
        assert prov["last_validated_at"].startswith("2026-05-01T10:00:00")

    def test_malformed_provenance_is_dropped_not_raised(self, eng: Engram):
        _run(mcp_server.add_lesson(
            summary="lesson with bad timestamp",
            source_agent="codex",
            last_validated_at="not-a-date",
            user_confirmed=True,
        ))
        found = [l for l in eng.get_lessons() if l["summary"] == "lesson with bad timestamp"]
        assert found
        prov = found[0]["provenance"]
        assert prov["source_agent"] == "codex"
        assert "last_validated_at" not in prov  # malformed → dropped

    def test_add_decision_stores_provenance(self, eng: Engram):
        _run(mcp_server.add_decision(
            question="cache layer?", choice="redis",
            source_agent="codex", run_id="run-9",
            user_confirmed=True,
        ))
        found = [d for d in eng.get_decisions() if d.get("question") == "cache layer?"]
        assert found
        assert found[0]["provenance"]["source_agent"] == "codex"
        assert found[0]["provenance"]["run_id"] == "run-9"

    def test_add_playbook_stores_provenance(self, eng: Engram):
        _run(mcp_server.add_playbook(
            title="release flow",
            triggers="release,publish",
            steps_json=json.dumps([
                {"order": 1, "action": "build"}, {"order": 2, "action": "ship"},
            ]),
            source_agent="claude_code",
            user_confirmed=True,
        ))
        pbs = eng.get_playbooks()
        found = [p for p in pbs if p.get("title") == "release flow"]
        assert found
        assert found[0]["provenance"]["source_agent"] == "claude_code"

    def test_existing_source_tool_still_works_alongside(self, eng: Engram):
        """source_tool (legacy) and source_agent (new) coexist."""
        _run(mcp_server.add_lesson(
            summary="coexist lesson", source_tool="cursor", source_agent="cursor-sub",
            user_confirmed=True,
        ))
        found = [l for l in eng.get_lessons() if l["summary"] == "coexist lesson"]
        assert found[0]["source_tool"] == "cursor"
        assert found[0]["provenance"]["source_agent"] == "cursor-sub"


# ---------------------------------------------------------------------------
# Follow-up B: recall-path opt-in freshness
# ---------------------------------------------------------------------------


class TestRecallFreshnessOptIn:
    def test_search_default_has_no_freshness(self, eng: Engram):
        eng.add_lesson({"summary": "searchable lesson about python testing"})
        result = json.loads(_run(mcp_server.search_knowledge("python")))
        for bucket in ("lessons", "decisions", "playbooks"):
            for item in result.get(bucket, []):
                assert "freshness" not in item

    def test_search_include_freshness_annotates(self, eng: Engram):
        eng.add_lesson({"summary": "searchable lesson about python testing"})
        result = json.loads(
            _run(mcp_server.search_knowledge("python", include_freshness=True))
        )
        annotated = result.get("lessons", [])
        assert annotated, "expected at least one lesson hit"
        assert all("freshness" in item for item in annotated)
        assert annotated[0]["freshness"]["freshness_status"] in (
            "fresh", "aging", "stale", "unknown",
        )

    def test_relevant_default_has_no_freshness(self, eng: Engram):
        eng.add_lesson({"summary": "python lesson", "domain": "python"})
        eng.save_project_snapshot("/proj", {"title": "P", "tech_stack": ["python"]})
        result = json.loads(_run(mcp_server.get_relevant_knowledge("/proj", limit=5)))
        for item in result.get("items", []):
            assert "freshness" not in item

    def test_relevant_include_freshness_annotates(self, eng: Engram):
        eng.add_lesson({"summary": "python lesson", "domain": "python"})
        eng.save_project_snapshot("/proj", {"title": "P", "tech_stack": ["python"]})
        result = json.loads(
            _run(mcp_server.get_relevant_knowledge("/proj", limit=5, include_freshness=True))
        )
        for item in result.get("items", []):
            assert "freshness" in item
