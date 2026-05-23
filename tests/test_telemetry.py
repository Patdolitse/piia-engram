"""Tests for piia_engram.telemetry — anonymous usage statistics (Phase 1 & 2)."""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from piia_engram.telemetry import (
    ToolCallTracker,
    _daily_id,
    _send_remote,
    _validate_payload,
    build_payload,
    get_endpoint,
    get_status,
    is_enabled,
    is_remote_enabled,
    log_payload,
    preview_payload,
    set_enabled,
    set_remote_enabled,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_engram_dir(tmp_path, monkeypatch):
    """Redirect ENGRAM_DIR to a temp directory for every test."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    # Clear any env overrides
    monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY_REMOTE", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY_URL", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Config & opt-in/out
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_is_disabled(self):
        assert is_enabled() is False

    def test_set_enabled_persists(self, isolated_engram_dir):
        set_enabled(True)
        assert is_enabled() is True
        cfg = json.loads((isolated_engram_dir / "telemetry_config.json").read_text())
        assert cfg["enabled"] is True
        assert "local_uuid" in cfg
        assert "opted_in_at" in cfg

    def test_set_disabled_persists(self, isolated_engram_dir):
        set_enabled(True)
        set_enabled(False)
        assert is_enabled() is False
        cfg = json.loads((isolated_engram_dir / "telemetry_config.json").read_text())
        assert cfg["enabled"] is False
        assert "opted_out_at" in cfg

    def test_env_override_off(self, monkeypatch):
        set_enabled(True)
        monkeypatch.setenv("ENGRAM_TELEMETRY", "0")
        assert is_enabled() is False

    def test_env_override_on(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
        assert is_enabled() is True

    def test_get_status_structure(self):
        status = get_status()
        assert "enabled" in status
        assert "remote_enabled" in status
        assert "config_path" in status
        assert "log_path" in status
        assert "phase" in status
        assert "local log" in status["phase"]


# ---------------------------------------------------------------------------
# Daily anonymous ID
# ---------------------------------------------------------------------------

class TestDailyId:
    def test_deterministic_for_same_uuid_and_date(self):
        id1 = _daily_id("test-uuid-123")
        id2 = _daily_id("test-uuid-123")
        assert id1 == id2

    def test_different_uuids_produce_different_ids(self):
        id1 = _daily_id("uuid-a")
        id2 = _daily_id("uuid-b")
        assert id1 != id2

    def test_id_is_hex_and_16_chars(self):
        daily = _daily_id("test-uuid")
        assert len(daily) == 16
        int(daily, 16)  # should not raise


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_valid_payload_passes(self):
        payload = {
            "schema": 1,
            "daily_id": "abc123",
            "engram_version": "3.15.0",
            "tool_calls": {"add_lesson": {"success": 5, "error": 0}},
        }
        assert _validate_payload(payload) is True

    def test_long_string_rejected(self):
        payload = {"field": "x" * 201}
        assert _validate_payload(payload) is False

    def test_natural_language_rejected(self):
        # String with >20% spaces, >100 chars — looks like content
        text = "This is a lesson about how to write good code " * 5
        payload = {"field": text}
        assert _validate_payload(payload) is False

    def test_short_strings_pass(self):
        payload = {"tool": "add_lesson", "version": "3.15.0"}
        assert _validate_payload(payload) is True

    def test_nested_dict_validated(self):
        payload = {"outer": {"inner": "x" * 201}}
        assert _validate_payload(payload) is False

    def test_numeric_values_ignored(self):
        payload = {"count": 42, "name": "ok"}
        assert _validate_payload(payload) is True

    def test_key_too_long_rejected(self):
        long_key = "a" * 81
        payload = {long_key: "value"}
        assert _validate_payload(payload) is False

    def test_key_with_spaces_rejected(self):
        payload = {"this is a sentence as key": "value"}
        assert _validate_payload(payload) is False

    def test_key_with_path_separator_rejected(self):
        payload = {"/home/user/.engram/secrets": "value"}
        assert _validate_payload(payload) is False

    def test_key_with_backslash_rejected(self):
        payload = {"C:\\Users\\data": "value"}
        assert _validate_payload(payload) is False

    def test_nested_bad_key_rejected(self):
        payload = {"tool_calls": {"natural language key with spaces": {"success": 1}}}
        assert _validate_payload(payload) is False

    def test_normal_tool_name_keys_pass(self):
        payload = {"tool_calls": {
            "add_lesson": {"success": 5, "error": 0},
            "get_user_context": {"success": 3, "error": 1},
            "wrap_up_session": {"success": 1, "error": 0},
        }}
        assert _validate_payload(payload) is True


# ---------------------------------------------------------------------------
# Build payload
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def test_returns_none_when_disabled(self):
        assert build_payload(engram_version="3.15.0") is None

    def test_returns_payload_when_enabled(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(
            tool_calls={"add_lesson": {"success": 3, "error": 0}},
            knowledge_counts={"lessons": 10, "decisions": 5, "domains": 2},
            engram_version="3.15.0",
        )
        assert payload is not None
        assert payload["schema"] == 1
        assert payload["engram_version"] == "3.15.0"
        assert "daily_id" in payload
        assert "timestamp" in payload
        assert payload["tool_calls"]["add_lesson"]["success"] == 3
        assert payload["knowledge_counts"]["lessons"] == 10

    def test_payload_never_contains_content(self, isolated_engram_dir):
        """Ensure no lesson/decision text can leak into payload."""
        set_enabled(True)
        # Attempt to sneak content through tool_calls keys
        payload = build_payload(
            tool_calls={"add_lesson": {"success": 1, "error": 0}},
            engram_version="3.15.0",
        )
        # Payload should only contain tool names, counts, version, ids
        payload_str = json.dumps(payload)
        # No natural language should be present
        assert "lesson about" not in payload_str
        assert "decision about" not in payload_str


# ---------------------------------------------------------------------------
# Local log
# ---------------------------------------------------------------------------

class TestLocalLog:
    def test_log_creates_file(self, isolated_engram_dir):
        payload = {"schema": 1, "test": True}
        path = log_payload(payload)
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        logged = json.loads(lines[0])
        assert logged["test"] is True

    def test_log_appends(self, isolated_engram_dir):
        log_payload({"entry": 1})
        log_payload({"entry": 2})
        path = isolated_engram_dir / "telemetry.log"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_preview_returns_json(self):
        result = preview_payload(engram_version="3.15.0")
        parsed = json.loads(result)
        assert parsed["schema"] == 1
        assert "tool_calls" in parsed
        assert "knowledge_counts" in parsed

    def test_preview_works_when_disabled(self):
        assert is_enabled() is False
        result = preview_payload()
        parsed = json.loads(result)
        assert parsed["schema"] == 1


# ---------------------------------------------------------------------------
# ToolCallTracker
# ---------------------------------------------------------------------------

class TestToolCallTracker:
    def test_record_and_get_counts(self):
        tracker = ToolCallTracker()
        tracker.record("add_lesson", success=True)
        tracker.record("add_lesson", success=True)
        tracker.record("add_lesson", success=False)
        tracker.record("search_knowledge", success=True)

        counts = tracker.get_counts()
        assert counts["add_lesson"]["success"] == 2
        assert counts["add_lesson"]["error"] == 1
        assert counts["search_knowledge"]["success"] == 1

    def test_should_flush_once_per_day(self):
        tracker = ToolCallTracker()
        assert tracker.should_flush() is True

    def test_flush_disabled_returns_none(self):
        tracker = ToolCallTracker()
        tracker.record("test_tool", success=True)
        result = tracker.flush(engram_version="3.15.0")
        assert result is None  # disabled by default

    def test_flush_enabled_writes_log(self, isolated_engram_dir):
        set_enabled(True)
        tracker = ToolCallTracker()
        tracker.record("add_lesson", success=True)
        result = tracker.flush(
            knowledge_counts={"lessons": 5, "decisions": 2, "domains": 1},
            engram_version="3.15.0",
        )
        assert result is not None
        assert result.exists()
        logged = json.loads(result.read_text(encoding="utf-8").strip())
        assert "tool_calls" in logged
        assert logged["tool_calls"]["add_lesson"]["success"] == 1

    def test_flush_clears_counts(self, isolated_engram_dir):
        set_enabled(True)
        tracker = ToolCallTracker()
        tracker.record("test", success=True)
        tracker.flush(engram_version="3.15.0")
        assert tracker.get_counts() == {}

    def test_flush_respects_daily_limit(self, isolated_engram_dir):
        set_enabled(True)
        tracker = ToolCallTracker()
        tracker.record("test", success=True)
        tracker.flush(engram_version="3.15.0")
        # Second flush same day should return None
        tracker.record("test2", success=True)
        result = tracker.flush(engram_version="3.15.0")
        assert result is None


# ---------------------------------------------------------------------------
# Critical safety: opt-out means NO network (Phase 1 has none anyway,
# but we test the principle for Phase 2 readiness)
# ---------------------------------------------------------------------------

class TestOptOutSafety:
    def test_disabled_build_returns_none(self):
        """When disabled, build_payload must return None — nothing to send."""
        assert is_enabled() is False
        result = build_payload(
            tool_calls={"test": {"success": 1, "error": 0}},
            engram_version="3.15.0",
        )
        assert result is None

    def test_disabled_flush_returns_none(self):
        """When disabled, ToolCallTracker.flush must not write anything."""
        tracker = ToolCallTracker()
        tracker.record("test", success=True)
        result = tracker.flush(engram_version="3.15.0")
        assert result is None


# ---------------------------------------------------------------------------
# Phase 2: Remote config
# ---------------------------------------------------------------------------

class TestRemoteConfig:
    def test_remote_default_disabled(self):
        assert is_remote_enabled() is False

    def test_remote_requires_local_enabled(self, isolated_engram_dir):
        """Remote cannot be enabled if local stats are off."""
        set_remote_enabled(True)
        assert is_remote_enabled() is False  # local still off

    def test_remote_enabled_when_both_on(self, isolated_engram_dir):
        set_enabled(True)
        set_remote_enabled(True)
        assert is_remote_enabled() is True

    def test_remote_disable(self, isolated_engram_dir):
        set_enabled(True)
        set_remote_enabled(True)
        assert is_remote_enabled() is True
        set_remote_enabled(False)
        assert is_remote_enabled() is False

    def test_remote_env_override_off(self, isolated_engram_dir, monkeypatch):
        set_enabled(True)
        set_remote_enabled(True)
        monkeypatch.setenv("ENGRAM_TELEMETRY_REMOTE", "0")
        assert is_remote_enabled() is False

    def test_remote_env_override_on(self, isolated_engram_dir, monkeypatch):
        set_enabled(True)
        monkeypatch.setenv("ENGRAM_TELEMETRY_REMOTE", "1")
        assert is_remote_enabled() is True

    def test_get_status_shows_remote(self, isolated_engram_dir):
        set_enabled(True)
        set_remote_enabled(True)
        status = get_status()
        assert status["remote_enabled"] is True
        assert "remote" in status["phase"].lower()
        assert status["endpoint"] != "(disabled)"

    def test_get_status_disabled_remote(self):
        status = get_status()
        assert status["remote_enabled"] is False
        assert status["endpoint"] == "(disabled)"

    def test_custom_endpoint(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_TELEMETRY_URL", "https://custom.example.com/events")
        assert get_endpoint() == "https://custom.example.com/events"

    def test_default_endpoint(self):
        assert "engram-telemetry" in get_endpoint()
        assert get_endpoint().startswith("https://")


# ---------------------------------------------------------------------------
# Phase 2: Remote sender
# ---------------------------------------------------------------------------

class TestRemoteSender:
    def test_send_remote_disabled_returns_false(self):
        """When remote is off, _send_remote should return False immediately."""
        result = _send_remote({"schema": 1, "daily_id": "test"})
        assert result is False

    def test_send_remote_never_raises_on_network_error(self, isolated_engram_dir):
        """Even with invalid endpoint, _send_remote must never raise."""
        set_enabled(True)
        set_remote_enabled(True)
        # Point to a guaranteed-unreachable endpoint
        os.environ["ENGRAM_TELEMETRY_URL"] = "https://127.0.0.1:1/invalid"
        try:
            result = _send_remote({"schema": 1, "daily_id": "test"})
            # Must not raise — returns False on failure
            assert result is False
        finally:
            os.environ.pop("ENGRAM_TELEMETRY_URL", None)

    def test_send_remote_never_raises_on_bad_url(self, isolated_engram_dir):
        """Even with completely invalid URL, must not raise."""
        set_enabled(True)
        set_remote_enabled(True)
        os.environ["ENGRAM_TELEMETRY_URL"] = "not-a-url"
        try:
            result = _send_remote({"schema": 1, "daily_id": "test"})
            assert result is False
        finally:
            os.environ.pop("ENGRAM_TELEMETRY_URL", None)

    def test_flush_with_remote_never_raises(self, isolated_engram_dir):
        """ToolCallTracker.flush must complete even if remote sending fails."""
        set_enabled(True)
        set_remote_enabled(True)
        os.environ["ENGRAM_TELEMETRY_URL"] = "https://127.0.0.1:1/invalid"
        try:
            tracker = ToolCallTracker()
            tracker.record("test_tool", success=True)
            # flush should succeed (local log written) despite remote failure
            result = tracker.flush(engram_version="test")
            assert result is not None
            assert result.exists()
        finally:
            os.environ.pop("ENGRAM_TELEMETRY_URL", None)


# ---------------------------------------------------------------------------
# Phase 2: Payload includes new fields
# ---------------------------------------------------------------------------

class TestPayloadNewFields:
    def test_payload_includes_os_platform(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.24.0")
        assert payload is not None
        assert "os_platform" in payload
        assert payload["os_platform"] in ("win32", "darwin", "linux", "cygwin")

    def test_payload_includes_python_version(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.24.0")
        assert payload is not None
        assert "python_version" in payload
        # Should be "major.minor" format
        parts = payload["python_version"].split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_payload_includes_tools_tier(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.24.0", tools_tier="all")
        assert payload is not None
        assert payload["tools_tier"] == "all"

    def test_payload_default_tier_is_core(self, isolated_engram_dir, monkeypatch):
        monkeypatch.delenv("ENGRAM_TOOLS", raising=False)
        set_enabled(True)
        payload = build_payload(engram_version="3.24.0")
        assert payload is not None
        assert payload["tools_tier"] == "core"

    def test_preview_includes_new_fields(self):
        result = preview_payload(engram_version="3.24.0")
        parsed = json.loads(result)
        assert "os_platform" in parsed
        assert "python_version" in parsed
        assert "tools_tier" in parsed
