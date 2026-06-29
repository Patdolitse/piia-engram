"""Tests for permission profile a4: write-path governance gate.

a4 adds ``maybe_refuse_write()`` in ``governance_runtime.py`` and injects
a pre-execution refusal guard into every MCP write tool.  When governance is
ON and the caller's write policy is ``"no"`` (read-only-external), the tool
returns a refusal string without touching the knowledge base.

Callers with ``"verified"`` (private-self) or ``"direct_write"``
(trusted-local) are allowed through; high-blast operations are owner-gated
separately.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_engram(tmp_path: Path):
    engram = tmp_path / "engram"
    identity = engram / "identity"
    identity.mkdir(parents=True)
    (identity / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}),
        encoding="utf-8",
    )
    knowledge = engram / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "decisions.json").write_text("[]", encoding="utf-8")
    return engram


def _make_engram(engram_dir: Path):
    import sys
    sys.path.insert(0, str(_ROOT / "src"))
    from piia_engram.core import Engram
    return Engram(engram_dir)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# maybe_refuse_write (unit tests on governance_runtime)
# ---------------------------------------------------------------------------


class TestMaybeRefuseWrite:
    """Direct tests for governance_runtime.maybe_refuse_write."""

    def test_governance_off_allows_write(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is None

    def test_owner_allows_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is None

    def test_trusted_local_allows_write(self, tmp_path, monkeypatch):
        """trusted-local (direct_write) is allowed."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is None

    def test_external_refuses_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is not None
        assert "治理层" in result
        assert "write" in result.lower() or "写入" in result

    def test_revoked_agent_refuses_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        from piia_engram.governance_store import GrantStore
        store = GrantStore(tmp_path)
        store.revoke("cli")
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(
            tmp_path, tool="add_lesson", agent_id="cli"
        )
        assert result is not None

    def test_unknown_client_refuses_write(self, tmp_path, monkeypatch):
        """Unknown/empty client type → read-only-external → write refused."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "")
        from piia_engram import governance_runtime as grt
        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is not None


# ---------------------------------------------------------------------------
# MCP write tools: refusal for read-only-external
# ---------------------------------------------------------------------------


# List of (tool_func_name, kwargs) for all gated write tools
_WRITE_TOOLS = [
    ("add_lesson", {"summary": "test lesson"}),
    ("add_decision", {"question": "q", "choice": "c"}),
    ("add_playbook", {"title": "test pb", "triggers": "a,b"}),
    ("memory_store", {"kind": "lesson", "content_json": '{"summary": "x"}'}),
    ("update_knowledge", {"item_id": "fake-id", "updates_json": "{}"}),
    ("archive_knowledge", {"item_id": "fake-id"}),
    ("review_staging", {"action": "review_item", "knowledge_id": "fake-id"}),
    ("merge_knowledge", {"primary_id": "a", "secondary_id": "b"}),
    ("manage_relation", {"action": "link", "src_id": "a", "dst_id": "b"}),
    ("manage_relation", {"action": "unlink", "src_id": "a", "dst_id": "b"}),
    ("manage_playbook", {"action": "update", "playbook_id": "fake-id", "status": "active"}),
    ("manage_playbook", {"action": "archive", "playbook_id": "fake-id"}),
    ("update_identity", {"field": "profile", "updates_json": '{"role": "x"}'}),
    ("register_tool", {"name": "test-tool"}),
    ("save_project_snapshot", {"project_folder": "/test", "data_json": '{"title": "t"}'}),
    ("playbook_execution", {"action": "update_step", "playbook_id": "fake-id", "step_order": 1, "step_status": "completed"}),
    ("save_agent_context", {"tool": "test", "content": "ctx"}),
    ("start_project", {"description": "new", "project_folder": "/test"}),
]


class TestWriteToolsRefuseExternal:
    """All gated write tools should refuse for read-only-external callers."""

    @pytest.mark.parametrize("tool_name,kwargs", _WRITE_TOOLS,
                             ids=[t[0] for t in _WRITE_TOOLS])
    def test_external_write_refused(self, tmp_path, monkeypatch, tool_name, kwargs):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(getattr(mcp_server, tool_name)(**kwargs))
        assert "治理层" in result, f"{tool_name} did not refuse external write"
        assert "write" in result.lower() or "写入" in result


class TestWriteToolsAllowOwner:
    """Owner (private-self) should be allowed to write."""

    def test_add_lesson_owner_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(mcp_server.add_lesson(summary="test lesson from owner", user_confirmed=True))
        assert "治理层" not in result
        assert "教训已记录" in result or "已记录" in result

    def test_add_decision_owner_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(mcp_server.add_decision(question="q", choice="c", user_confirmed=True))
        assert "治理层" not in result
        assert "决策已记录" in result

    def test_memory_store_owner_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(mcp_server.memory_store(
            kind="lesson",
            content_json='{"summary": "test via memory_store"}',
            user_confirmed=True,
        ))
        assert "治理层" not in result


class TestWriteToolsAllowTrustedLocal:
    """trusted-local should be allowed to write (direct_write)."""

    def test_add_lesson_trusted_local_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(mcp_server.add_lesson(summary="lesson from trusted agent", user_confirmed=True))
        assert "治理层" not in result
        assert "教训已记录" in result or "已记录" in result


class TestWriteToolsGovernanceOff:
    """When governance is OFF, all writes should proceed normally."""

    def test_add_lesson_governance_off(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        result = _run(mcp_server.add_lesson(summary="lesson with gov off", user_confirmed=True))
        assert "治理层" not in result
        assert "教训已记录" in result


# ---------------------------------------------------------------------------
# Coverage: write gate records audit receipt
# ---------------------------------------------------------------------------


class TestWriteGateAudit:
    """maybe_refuse_write should record a governance receipt when refusing."""

    def test_refusal_creates_receipt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        from piia_engram import governance_runtime as grt

        result = grt.maybe_refuse_write(tmp_path, tool="add_lesson")
        assert result is not None

        # Check receipt was written
        receipt_dir = tmp_path / "governance" / "receipts"
        if receipt_dir.exists():
            receipts = list(receipt_dir.glob("*.json"))
            assert len(receipts) >= 1
            receipt_data = json.loads(receipts[0].read_text(encoding="utf-8"))
            assert receipt_data.get("tool") == "add_lesson"
            assert receipt_data.get("trust_level") == "read-only-external"
