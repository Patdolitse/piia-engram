"""Tests for permission profile a3: caller permissions in search & retrieval results.

a3 embeds ``_caller_permissions`` into the JSON responses of
``search_knowledge`` and ``get_relevant_knowledge`` so consuming AI tools
know their governance context from every retrieval call.

Design:
- search_knowledge returns a dict → inject ``_caller_permissions`` key.
- get_relevant_knowledge returns a wrapped dict
  ``{"items": [...], "_caller_permissions": {...}}``.
"""

import json
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_engram(tmp_path: Path):
    """Create a minimal Engram with one lesson so search can return results."""
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
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# search_knowledge: _caller_permissions injected into result dict
# ---------------------------------------------------------------------------


class TestSearchKnowledgePermissions:
    """a3: search_knowledge embeds _caller_permissions in JSON result."""

    def test_search_result_has_permissions_governance_off(self, tmp_path, monkeypatch):
        """Governance OFF: _caller_permissions present with governance_enabled=False."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "test lesson for search"})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("test"))
        parsed = json.loads(raw)

        assert "_caller_permissions" in parsed
        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is False
        assert perms["trust_level"] == "unrestricted"
        assert perms["max_sensitivity"] == "all"

    def test_search_result_has_permissions_governance_on_owner(self, tmp_path, monkeypatch):
        """Governance ON + owner: _caller_permissions reflects private-self."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "test lesson for search"})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("test"))
        parsed = json.loads(raw)

        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is True
        assert perms["trust_level"] == "private-self"
        assert perms["revoked"] is False

    def test_search_result_has_permissions_governance_on_external(self, tmp_path, monkeypatch):
        """Governance ON + external: _caller_permissions reflects read-only-external."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "test lesson for search"})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("test"))
        parsed = json.loads(raw)

        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is True
        assert perms["trust_level"] == "read-only-external"

    def test_search_empty_result_still_has_permissions(self, tmp_path, monkeypatch):
        """Even when search returns no matches, _caller_permissions is present."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("nonexistent_xyz"))
        parsed = json.loads(raw)

        assert "_caller_permissions" in parsed
        assert parsed["lessons"] == []
        assert parsed["decisions"] == []

    def test_search_permissions_no_knowledge_leak(self, tmp_path, monkeypatch):
        """_caller_permissions must never contain actual knowledge content."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "super secret internal info"})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("secret"))
        parsed = json.loads(raw)

        perms_str = json.dumps(parsed["_caller_permissions"])
        assert "super secret" not in perms_str
        assert "internal info" not in perms_str


# ---------------------------------------------------------------------------
# get_relevant_knowledge: wrapped dict with _caller_permissions
# ---------------------------------------------------------------------------


class TestGetRelevantKnowledgePermissions:
    """a3: get_relevant_knowledge embeds _caller_permissions."""

    def test_relevant_empty_has_permissions(self, tmp_path, monkeypatch):
        """Empty result still includes _caller_permissions."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.get_relevant_knowledge(
            project_folder="/nonexistent", limit=5
        ))
        parsed = json.loads(raw)

        assert "_caller_permissions" in parsed
        assert parsed["items"] == []
        assert "尚无" in parsed.get("note", "")

    def test_relevant_with_data_has_permissions(self, tmp_path, monkeypatch):
        """Result with lessons includes _caller_permissions."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "python pattern", "domain": "python"})
        e.save_project_snapshot("/proj", {"title": "P", "tech_stack": ["python"]})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.get_relevant_knowledge(
            project_folder="/proj", limit=5
        ))
        parsed = json.loads(raw)

        assert "_caller_permissions" in parsed
        assert "items" in parsed
        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is False
        assert perms["trust_level"] == "unrestricted"

    def test_relevant_governance_on_owner(self, tmp_path, monkeypatch):
        """Governance ON + owner: permissions reflect trust level."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)
        e.add_lesson({"summary": "python tip", "domain": "python"})
        e.save_project_snapshot("/proj", {"title": "P", "tech_stack": ["python"]})

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.get_relevant_knowledge(
            project_folder="/proj", limit=5
        ))
        parsed = json.loads(raw)

        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is True
        assert perms["trust_level"] == "private-self"

    def test_relevant_governance_on_external(self, tmp_path, monkeypatch):
        """Governance ON + external: permissions show access ceiling."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.get_relevant_knowledge(
            project_folder="/proj", limit=5
        ))
        parsed = json.loads(raw)

        perms = parsed["_caller_permissions"]
        assert perms["governance_enabled"] is True
        assert perms["trust_level"] == "read-only-external"


# ---------------------------------------------------------------------------
# Cross-cutting: permissions structure consistency
# ---------------------------------------------------------------------------


class TestPermissionsStructureConsistency:
    """Verify _caller_permissions has consistent shape across endpoints."""

    _REQUIRED_KEYS = {"governance_enabled", "trust_level", "max_sensitivity",
                      "write_policy", "revoked"}

    def test_search_permissions_has_all_keys_gov_off(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("x"))
        parsed = json.loads(raw)
        perms = parsed["_caller_permissions"]
        assert self._REQUIRED_KEYS.issubset(perms.keys())

    def test_search_permissions_has_all_keys_gov_on(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("x"))
        parsed = json.loads(raw)
        perms = parsed["_caller_permissions"]
        assert self._REQUIRED_KEYS.issubset(perms.keys())
        # governance ON also has agent_id
        assert "agent_id" in perms

    def test_relevant_permissions_has_all_keys(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server
        mcp_server._engram = e

        raw = _run(mcp_server.get_relevant_knowledge("/x", limit=5))
        parsed = json.loads(raw)
        perms = parsed["_caller_permissions"]
        assert self._REQUIRED_KEYS.issubset(perms.keys())

    def test_permissions_match_describe_caller(self, tmp_path, monkeypatch):
        """_caller_permissions in search should match describe_caller_permissions."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "codex")
        engram = _setup_engram(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        e = _make_engram(engram)

        from piia_engram import mcp_server, governance_runtime as grt
        mcp_server._engram = e

        raw = _run(mcp_server.search_knowledge("x"))
        parsed = json.loads(raw)
        embedded = parsed["_caller_permissions"]

        direct = grt.describe_caller_permissions(engram)

        assert embedded["governance_enabled"] == direct["governance_enabled"]
        assert embedded["trust_level"] == direct["trust_level"]
        assert embedded["max_sensitivity"] == direct["max_sensitivity"]
        assert embedded["write_policy"] == direct["write_policy"]
        assert embedded["revoked"] == direct["revoked"]
