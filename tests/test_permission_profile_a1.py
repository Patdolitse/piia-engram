"""Tests for permission profile a1: caller permissions embedded in get_user_context.

a1 adds a `## Caller Permissions` section to the cold-start context so the
consuming AI tool knows its governance status and trust boundary from the
first message, without a separate MCP call.
"""

import json
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent


def _setup_identity(tmp_path: Path) -> Path:
    """Create a minimal Engram data tree so generate_context returns content."""
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


def _make_retrieval(engram_dir: Path):
    """Create a Retrieval instance pointing at the given directory."""
    import sys
    sys.path.insert(0, str(_ROOT / "src"))
    from piia_engram.core import Engram
    return Engram(engram_dir)


# ---------------------------------------------------------------------------
# describe_caller_permissions (unit tests on governance_runtime)
# ---------------------------------------------------------------------------


class TestDescribeCallerPermissions:
    """Direct tests for governance_runtime.describe_caller_permissions."""

    def test_governance_off_returns_unrestricted(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["governance_enabled"] is False
        assert result["trust_level"] == "unrestricted"
        assert result["max_sensitivity"] == "all"
        assert result["revoked"] is False
        assert "ENGRAM_GOVERNANCE=1" in result["note"]

    def test_governance_on_private_self(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["governance_enabled"] is True
        assert result["trust_level"] == "private-self"
        assert result["max_sensitivity"] == "secret"
        assert result["write_policy"] == "verified"
        assert result["revoked"] is False

    def test_governance_on_trusted_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["governance_enabled"] is True
        assert result["trust_level"] == "trusted-local"
        assert result["max_sensitivity"] == "work"
        assert result["write_policy"] == "proposed_only"

    def test_vnext_context_narrows_described_profile(self, tmp_path, monkeypatch):
        """Phase 2: role/stage/depth must narrow the live described profile."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        monkeypatch.setenv("ENGRAM_CALLER_ROLE", "assistant")
        monkeypatch.setenv("ENGRAM_WORKFLOW_STAGE", "review")
        monkeypatch.setenv("ENGRAM_CALLER_DEPTH", "1")

        from piia_engram import governance_runtime as grt

        result = grt.describe_caller_permissions(tmp_path)

        assert result["trust_level"] == "private-self"
        assert result["trust_max_sensitivity"] == "secret"
        assert result["max_sensitivity"] == "public"
        assert result["write_policy"] == "proposed_only"
        assert result["permission_profile_vnext"]["caller_role"] == "assistant"
        assert result["permission_profile_vnext"]["workflow_stage"] == "review"
        assert result["permission_profile_vnext"]["caller_depth"] == 1
        assert "downgraded_by_depth" in result["permission_profile_vnext"]["reasons"]

    def test_governance_on_unknown_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "random_web_bot")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["governance_enabled"] is True
        assert result["trust_level"] == "read-only-external"
        assert result["max_sensitivity"] == "public"
        assert result["write_policy"] == "no"

    def test_governance_on_explicit_grant_overrides(self, tmp_path, monkeypatch):
        """An explicit GrantStore binding should win over auto-classification."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "random_web_bot")
        from piia_engram.governance_store import GrantStore
        store = GrantStore(tmp_path)
        store.set_grant("random_web_bot", "trusted-local")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["trust_level"] == "trusted-local"
        assert result["max_sensitivity"] == "work"

    def test_governance_on_revoked_caller(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
        from piia_engram.governance_store import GrantStore
        store = GrantStore(tmp_path)
        store.revoke("claude_code")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["revoked"] is True

    def test_note_mentions_sensitivity_ceiling(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cursor")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert "work" in result["note"]
        assert "filtered" in result["note"].lower()


# ---------------------------------------------------------------------------
# _format_permissions_section (unit tests on the renderer)
# ---------------------------------------------------------------------------


class TestFormatPermissionsSection:
    """Tests for the Markdown renderer used in get_user_context."""

    def test_governance_off_section(self):
        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from piia_engram.mcp_server import _format_permissions_section
        section = _format_permissions_section({
            "governance_enabled": False,
            "trust_level": "unrestricted",
        })
        assert "## Caller Permissions" in section
        assert "disabled" in section
        assert "调用方权限" in section  # bilingual

    def test_governance_on_section(self):
        from piia_engram.mcp_server import _format_permissions_section
        section = _format_permissions_section({
            "governance_enabled": True,
            "agent_id": "claude_code",
            "trust_level": "trusted-local",
            "max_sensitivity": "work",
            "write_policy": "proposed_only",
            "revoked": False,
            "note": "Items above 'work' sensitivity are filtered.",
        })
        assert "## Caller Permissions" in section
        assert "enabled" in section
        assert "`claude_code`" in section
        assert "`trusted-local`" in section
        assert "`work`" in section
        assert "proposed_only" in section

    def test_revoked_warning(self):
        from piia_engram.mcp_server import _format_permissions_section
        section = _format_permissions_section({
            "governance_enabled": True,
            "agent_id": "bad_bot",
            "trust_level": "read-only-external",
            "max_sensitivity": "public",
            "write_policy": "no",
            "revoked": True,
            "note": "",
        })
        assert "revoked" in section.lower()
        assert "⚠" in section

    def test_grant_error_shown(self):
        from piia_engram.mcp_server import _format_permissions_section
        section = _format_permissions_section({
            "governance_enabled": True,
            "agent_id": "x",
            "trust_level": "read-only-external",
            "max_sensitivity": "public",
            "write_policy": "no",
            "revoked": False,
            "grant_error": "grants.json corrupt",
            "note": "",
        })
        assert "grants.json corrupt" in section


# ---------------------------------------------------------------------------
# Integration: get_user_context includes permissions section
# ---------------------------------------------------------------------------


class TestGetUserContextIncludesPermissions:
    """Integration tests verifying the section appears in actual output."""

    def test_governance_off_context_has_permissions(self, tmp_path, monkeypatch):
        """Default (governance off): context should include permissions section."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_identity(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        r = _make_retrieval(engram)
        context = r.generate_context("", level="quick")
        assert context, "generate_context should return non-empty"

        # Simulate what get_user_context does: append permissions section
        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        full = context + _format_permissions_section(perms)
        assert "## Caller Permissions" in full
        assert "disabled" in full

    def test_governance_on_owner_context_has_permissions(self, tmp_path, monkeypatch):
        """Governance on + owner: full context includes permissions section."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_identity(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        r = _make_retrieval(engram)
        context = r.generate_context("", level="quick")

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        full = context + _format_permissions_section(perms)

        # Owner should see the full context through the gate
        governed = grt.maybe_govern_owner_only(engram, full, tool="get_user_context")
        assert "## Caller Permissions" in governed
        assert "private-self" in governed

    def test_governance_on_non_owner_gets_refusal(self, tmp_path, monkeypatch):
        """Governance on + non-owner: permissions section is NOT leaked."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "random_web_bot")
        engram = _setup_identity(tmp_path)
        monkeypatch.setenv("ENGRAM_DIR", str(engram))
        r = _make_retrieval(engram)
        context = r.generate_context("", level="quick")

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        full = context + _format_permissions_section(perms)

        governed = grt.maybe_govern_owner_only(engram, full, tool="get_user_context")
        # Non-owner should get refusal, NOT the permissions section
        assert "## Caller Permissions" not in governed
        assert "withheld" in governed.lower() or "trust" in governed.lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPermissionsEdgeCases:
    """Edge cases for the a1 permissions embedding."""

    def test_empty_client_type_falls_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(tmp_path)
        assert result["trust_level"] == "read-only-external"

    def test_all_known_clients_resolve(self, tmp_path, monkeypatch):
        """Every known client type should resolve to a valid trust level."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        from piia_engram import governance_runtime as grt
        from piia_engram.governance import TRUST_LEVELS
        for ct in ["cli", "self", "engram", "doctor",
                    "claude_code", "codex", "cursor", "windsurf", "gemini_cli",
                    "random_thing"]:
            monkeypatch.setenv("ENGRAM_CLIENT_TYPE", ct)
            result = grt.describe_caller_permissions(tmp_path)
            assert result["trust_level"] in TRUST_LEVELS or result["trust_level"] == "unrestricted"

    def test_permissions_section_does_not_contain_knowledge(self, tmp_path, monkeypatch):
        """Permissions section must never leak actual knowledge content."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(tmp_path)
        section = _format_permissions_section(perms)
        # Section should only contain governance metadata, never knowledge
        assert "lesson" not in section.lower() or "lessons" not in section.lower()
        assert "decision" not in section.lower()
        assert "playbook" not in section.lower()

    def test_describe_with_explicit_agent_id(self, tmp_path, monkeypatch):
        """Passing explicit agent_id should use it for resolution."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "")
        from piia_engram.governance_store import GrantStore
        store = GrantStore(tmp_path)
        store.set_grant("my-custom-agent", "trusted-local")
        from piia_engram import governance_runtime as grt
        result = grt.describe_caller_permissions(
            tmp_path, agent_id="my-custom-agent"
        )
        assert result["agent_id"] == "my-custom-agent"
        assert result["trust_level"] == "trusted-local"


# ---------------------------------------------------------------------------
# a2: get_resume_brief includes permissions section
# ---------------------------------------------------------------------------


class TestResumeBriefIncludesPermissions:
    """a2: verify the permissions section is embedded in get_resume_brief."""

    def test_resume_brief_has_permissions_governance_off(self, tmp_path, monkeypatch):
        """Default (governance off): resume brief markdown includes permissions."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_identity(tmp_path)
        r = _make_retrieval(engram)
        brief = r.get_resume_brief(project_folder="", token_budget=4000)
        assert isinstance(brief, dict)
        assert "markdown" in brief

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        perm_section = _format_permissions_section(perms)

        # Simulate what mcp_server does: inject before </engram-resume>
        md = brief["markdown"]
        close_tag = "</engram-resume>"
        assert close_tag in md, "Resume brief should have closing XML tag"
        injected = md.replace(close_tag, perm_section + "\n" + close_tag, 1)

        assert "## Caller Permissions" in injected
        assert "disabled" in injected
        # Permissions section should be INSIDE the engram-resume tags
        perm_pos = injected.index("Caller Permissions")
        close_pos = injected.index(close_tag)
        assert perm_pos < close_pos

    def test_resume_brief_owner_sees_permissions(self, tmp_path, monkeypatch):
        """Governance on + owner: resume brief passes gate with permissions."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
        engram = _setup_identity(tmp_path)
        r = _make_retrieval(engram)
        brief = r.get_resume_brief(project_folder="", token_budget=4000)

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        perm_section = _format_permissions_section(perms)
        md = brief["markdown"]
        close_tag = "</engram-resume>"
        brief["markdown"] = md.replace(close_tag, perm_section + "\n" + close_tag, 1)

        # Owner should pass the gate
        governed = grt.maybe_govern_owner_only(engram, brief, tool="get_resume_brief")
        assert isinstance(governed, dict)
        assert "Caller Permissions" in governed["markdown"]
        assert "private-self" in governed["markdown"]

    def test_resume_brief_non_owner_no_leak(self, tmp_path, monkeypatch):
        """Governance on + non-owner: permissions don't leak through gate."""
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "random_web_bot")
        engram = _setup_identity(tmp_path)
        r = _make_retrieval(engram)
        brief = r.get_resume_brief(project_folder="", token_budget=4000)

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        perm_section = _format_permissions_section(perms)
        md = brief["markdown"]
        close_tag = "</engram-resume>"
        brief["markdown"] = md.replace(close_tag, perm_section + "\n" + close_tag, 1)

        governed = grt.maybe_govern_owner_only(engram, brief, tool="get_resume_brief")
        # Non-owner gets withheld stub
        assert isinstance(governed, dict)
        assert "Caller Permissions" not in str(governed)
        assert governed.get("governance_withheld") is True

    def test_permissions_inside_engram_resume_tags(self, tmp_path, monkeypatch):
        """Permissions section must be inside the <engram-resume> wrapper."""
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        engram = _setup_identity(tmp_path)
        r = _make_retrieval(engram)
        brief = r.get_resume_brief(project_folder="", token_budget=4000)

        from piia_engram import governance_runtime as grt
        from piia_engram.mcp_server import _format_permissions_section
        perms = grt.describe_caller_permissions(engram)
        perm_section = _format_permissions_section(perms)
        md = brief["markdown"]
        close_tag = "</engram-resume>"
        injected = md.replace(close_tag, perm_section + "\n" + close_tag, 1)

        # Verify ordering: <engram-resume> ... Caller Permissions ... </engram-resume>
        open_pos = injected.index("<engram-resume")
        perm_pos = injected.index("Caller Permissions")
        close_pos = injected.index("</engram-resume>")
        assert open_pos < perm_pos < close_pos
