"""Permission Profile a0: get_permission_profile, set_caller_trust,
revoke_caller — user-facing control over the governance grant system.
"""

from __future__ import annotations

import pytest

from piia_engram.core import Engram


# ── get_permission_profile ────────────────────────────────────────────────

class TestGetPermissionProfile:
    def test_empty_grants(self, tmp_path):
        eng = Engram(root=tmp_path)
        pp = eng.get_permission_profile()
        assert pp["grants"] == {}
        assert pp["revoked"] == []
        assert "trust_levels" in pp
        assert "auto_rules" in pp

    def test_trust_levels_structure(self, tmp_path):
        eng = Engram(root=tmp_path)
        pp = eng.get_permission_profile()
        tl = pp["trust_levels"]
        assert "private-self" in tl
        assert "trusted-local" in tl
        assert "read-only-external" in tl
        # Each level has max_sensitivity, read, write
        for level in tl.values():
            assert "max_sensitivity" in level
            assert "read" in level
            assert "write" in level

    def test_auto_rules_include_known_clients(self, tmp_path):
        eng = Engram(root=tmp_path)
        pp = eng.get_permission_profile()
        rules = pp["auto_rules"]
        assert rules["claude_code"] == "trusted-local"
        assert rules["cursor"] == "trusted-local"
        assert rules["codex"] == "trusted-local"
        assert rules["self"] == "private-self"
        assert rules["cli"] == "private-self"
        assert rules["(unknown)"] == "read-only-external"

    def test_governance_enabled_field(self, tmp_path, monkeypatch):
        eng = Engram(root=tmp_path)
        monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
        pp = eng.get_permission_profile()
        assert pp["governance_enabled"] is False

        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        pp2 = eng.get_permission_profile()
        assert pp2["governance_enabled"] is True

    def test_shows_explicit_grants(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.set_caller_trust("my-cursor", "trusted-local")
        pp = eng.get_permission_profile()
        assert pp["grants"]["my-cursor"] == "trusted-local"

    def test_shows_revoked(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.revoke_caller("bad-agent")
        pp = eng.get_permission_profile()
        assert "bad-agent" in pp["revoked"]


# ── set_caller_trust ──────────────────────────────────────────────────────

class TestSetCallerTrust:
    def test_set_valid_level(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.set_caller_trust("test-agent", "trusted-local")
        assert result["success"] is True
        assert result["agent_id"] == "test-agent"
        assert result["trust_level"] == "trusted-local"

    def test_set_private_self(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.set_caller_trust("admin", "private-self")
        assert result["success"] is True

    def test_invalid_level_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.set_caller_trust("agent", "super-admin")
        assert result["success"] is False
        assert "error" in result
        assert "valid_levels" in result

    def test_empty_agent_id_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.set_caller_trust("", "trusted-local")
        assert result["success"] is False

    def test_whitespace_agent_id_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.set_caller_trust("   ", "trusted-local")
        assert result["success"] is False

    def test_grant_persists(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.set_caller_trust("persistent-agent", "private-self")
        # Fresh instance
        eng2 = Engram(root=tmp_path)
        pp = eng2.get_permission_profile()
        assert pp["grants"]["persistent-agent"] == "private-self"

    def test_grant_overwrites_previous(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.set_caller_trust("agent", "read-only-external")
        eng.set_caller_trust("agent", "trusted-local")
        pp = eng.get_permission_profile()
        assert pp["grants"]["agent"] == "trusted-local"

    def test_grant_clears_revocation(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.revoke_caller("agent")
        pp = eng.get_permission_profile()
        assert "agent" in pp["revoked"]
        # Re-grant should clear revocation
        eng.set_caller_trust("agent", "trusted-local")
        pp2 = eng.get_permission_profile()
        assert "agent" not in pp2["revoked"]
        assert pp2["grants"]["agent"] == "trusted-local"


# ── revoke_caller ─────────────────────────────────────────────────────────

class TestRevokeCaller:
    def test_revoke_success(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.revoke_caller("untrusted-agent")
        assert result["success"] is True
        assert result["agent_id"] == "untrusted-agent"
        assert result["revoked"] is True

    def test_revoke_empty_id_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.revoke_caller("")
        assert result["success"] is False

    def test_revoke_persists(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.revoke_caller("banned")
        # Fresh instance
        eng2 = Engram(root=tmp_path)
        pp = eng2.get_permission_profile()
        assert "banned" in pp["revoked"]

    def test_revoke_idempotent(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.revoke_caller("agent")
        eng.revoke_caller("agent")
        pp = eng.get_permission_profile()
        # Should only appear once
        assert pp["revoked"].count("agent") == 1

    def test_revoked_agent_in_governance_check(self, tmp_path):
        """A revoked agent should be denied by the governance gate."""
        from piia_engram.governance_store import GrantStore
        eng = Engram(root=tmp_path)
        eng.revoke_caller("bad-bot")
        store = GrantStore(tmp_path)
        assert store.is_revoked("bad-bot") is True


# ── integration ───────────────────────────────────────────────────────────

class TestPermissionProfileIntegration:
    def test_full_lifecycle(self, tmp_path):
        """Grant → verify → revoke → re-grant lifecycle."""
        eng = Engram(root=tmp_path)

        # 1. Initially empty
        pp = eng.get_permission_profile()
        assert pp["grants"] == {}
        assert pp["revoked"] == []

        # 2. Grant a caller
        eng.set_caller_trust("cursor-instance", "trusted-local")
        pp = eng.get_permission_profile()
        assert pp["grants"]["cursor-instance"] == "trusted-local"

        # 3. Revoke
        eng.revoke_caller("cursor-instance")
        pp = eng.get_permission_profile()
        assert "cursor-instance" in pp["revoked"]

        # 4. Re-grant (should clear revocation)
        eng.set_caller_trust("cursor-instance", "private-self")
        pp = eng.get_permission_profile()
        assert pp["grants"]["cursor-instance"] == "private-self"
        assert "cursor-instance" not in pp["revoked"]

    def test_multiple_callers(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.set_caller_trust("claude-code", "trusted-local")
        eng.set_caller_trust("web-client", "read-only-external")
        eng.set_caller_trust("admin-cli", "private-self")
        eng.revoke_caller("suspicious-bot")

        pp = eng.get_permission_profile()
        assert len(pp["grants"]) == 3
        assert pp["grants"]["claude-code"] == "trusted-local"
        assert pp["grants"]["web-client"] == "read-only-external"
        assert pp["grants"]["admin-cli"] == "private-self"
        assert "suspicious-bot" in pp["revoked"]
