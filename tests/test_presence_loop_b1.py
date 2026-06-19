"""Presence loop — Build 1 (Layer 2): branded save ACK + branded recall header.

Design locked with Codex (2026-06-19). Cross-client reality: MCP can't control
colour, so presence is a TEXT marker ``[Engram]``.

Save ACK (mcp_tools_write.py, 6 success-return sites — dispatcher + 3 tools):
    ``[Engram] <中文已记录> · tier={tier} · 可召回: {echo}``
  - keeps the existing Chinese fragment (so substring tests still pass),
  - keeps the caller-supplied echo (success-path echo is not a leak),
  - surfaces the real ``result.get("tier", "staging")``.

Recall header (recall_service.render_recall_text) — prepended top line:
    ``[Engram Recall] {N} memories · {fresh} fresh · {stale} stale``
  - N = len(knowledge); fresh/stale from freshness_status (aging/unknown uncounted);
  - degrades to ``[Engram Recall] {N} memories`` when no item carries freshness
    (include_freshness=False) or knowledge is empty.
  - NEVER prints "verified" — the recall payload projects no tier, so a verified
    count would imply trust we cannot substantiate (Codex's #1 risk).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram import recall_service as rs
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Save ACK — individual tools
# ---------------------------------------------------------------------------


class TestBrandedSaveAck:
    def test_add_lesson_ack_branded(self, eng: Engram):
        result = _run(mcp_server.add_lesson(summary="brand probe lesson"))
        assert result.startswith("[Engram] ")
        assert "教训已记录" in result          # Chinese kept (owner experience)
        assert "· tier=" in result            # real tier surfaced
        assert "· 可召回: " in result          # recall nudge
        assert "brand probe lesson" in result  # caller echo kept

    def test_add_decision_ack_branded(self, eng: Engram):
        result = _run(mcp_server.add_decision(question="brand q", choice="brand c"))
        assert result.startswith("[Engram] ")
        assert "决策已记录" in result
        assert "· tier=" in result
        assert "· 可召回: " in result
        assert "brand q" in result and "brand c" in result

    def test_add_playbook_ack_branded(self, eng: Engram):
        result = _run(mcp_server.add_playbook(title="brand pb", triggers="t1,t2"))
        assert result.startswith("[Engram] ")
        assert "Playbook 已记录" in result
        assert "· tier=" in result
        assert "· 可召回: " in result
        assert "brand pb" in result

    # ---- dispatcher (memory_store) ----

    def test_memory_store_lesson_ack_branded(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="lesson", content_json=json.dumps({"summary": "ms brand lesson"}),
        ))
        assert result.startswith("[Engram] ")
        assert "教训已记录" in result
        assert "· tier=" in result
        assert "· 可召回: " in result
        assert "ms brand lesson" in result

    def test_memory_store_decision_ack_branded(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="decision",
            content_json=json.dumps({"question": "ms q", "choice": "ms c"}),
        ))
        assert result.startswith("[Engram] ")
        assert "决策已记录" in result
        assert "· tier=" in result
        assert "· 可召回: " in result

    def test_memory_store_playbook_ack_branded(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="playbook",
            content_json=json.dumps({"title": "ms pb", "triggers": "a,b"}),
        ))
        assert result.startswith("[Engram] ")
        assert "Playbook 已记录" in result
        assert "· tier=" in result
        assert "· 可召回: " in result


# ---------------------------------------------------------------------------
# Recall header — render_recall_text
# ---------------------------------------------------------------------------


class TestBrandedRecallHeader:
    def test_header_with_freshness_counts(self):
        payload = {
            "meta": {"project": "proj"},
            "knowledge": [
                {"type": "lesson", "summary": "a", "freshness": {"freshness_status": "fresh"}},
                {"type": "lesson", "summary": "b", "freshness": {"freshness_status": "fresh"}},
                {"type": "lesson", "summary": "c", "freshness": {"freshness_status": "stale"}},
            ],
        }
        text = rs.render_recall_text(payload)
        first = text.splitlines()[0]
        assert first == "[Engram Recall] 3 memories · 2 fresh · 1 stale"
        # existing digest content must still follow below the brand header
        assert "Recall digest" in text

    def test_header_aging_and_unknown_not_counted(self):
        payload = {
            "meta": {},
            "knowledge": [
                {"type": "lesson", "summary": "a", "freshness": {"freshness_status": "aging"}},
                {"type": "lesson", "summary": "b", "freshness": {"freshness_status": "unknown"}},
                {"type": "lesson", "summary": "c", "freshness": {"freshness_status": "fresh"}},
            ],
        }
        first = rs.render_recall_text(payload).splitlines()[0]
        assert first == "[Engram Recall] 3 memories · 1 fresh · 0 stale"

    def test_header_degrades_without_freshness(self):
        payload = {
            "meta": {},
            "knowledge": [
                {"type": "lesson", "summary": "a"},
                {"type": "lesson", "summary": "b"},
            ],
        }
        first = rs.render_recall_text(payload).splitlines()[0]
        assert first == "[Engram Recall] 2 memories"

    def test_header_empty_knowledge(self):
        payload = {"meta": {}, "knowledge": []}
        first = rs.render_recall_text(payload).splitlines()[0]
        assert first == "[Engram Recall] 0 memories"

    def test_header_never_prints_verified(self):
        payload = {
            "meta": {},
            "knowledge": [
                {"type": "lesson", "summary": "a", "freshness": {"freshness_status": "fresh"}},
            ],
        }
        first = rs.render_recall_text(payload).splitlines()[0]
        assert "verified" not in first
