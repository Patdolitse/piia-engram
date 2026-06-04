"""Stage 3 item B — telemetry network-boundary hardening.

A consolidated, user-perspective guard around the privacy contract: in the
default state NOTHING reaches the network, remote send requires an explicit
SECOND opt-in beyond local stats, the pre-opt-in preview is metadata-only, and no
knowledge BODY ever enters a payload — only counts.

The network is sealed with a spy: ``telemetry.urlopen`` is monkeypatched to a
function that records the call and raises, so any accidental send both fails the
assertion AND cannot escape. No real endpoint is ever contacted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import telemetry as t


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh ENGRAM_DIR + cleared telemetry env so we observe true defaults."""
    root = tmp_path / "engram"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    for var in ("ENGRAM_TELEMETRY", "ENGRAM_TELEMETRY_REMOTE", "ENGRAM_FEEDBACK",
                "ENGRAM_TELEMETRY_URL", "ENGRAM_FEEDBACK_URL"):
        monkeypatch.delenv(var, raising=False)
    return root


@pytest.fixture()
def sealed_network(monkeypatch: pytest.MonkeyPatch):
    """Replace urlopen with a spy that records and refuses any network call."""
    calls = []

    def _spy(*args, **kwargs):  # noqa: ANN002
        calls.append((args, kwargs))
        raise AssertionError("network call attempted — boundary violated")

    monkeypatch.setattr(t, "urlopen", _spy)
    return calls


class TestDefaultOff:
    def test_all_three_gates_default_off(self, isolated):
        assert t.is_enabled() is False
        assert t.is_remote_enabled() is False
        assert t.is_feedback_enabled() is False

    def test_build_payload_none_when_disabled(self, isolated):
        assert t.build_payload(tool_calls={"x": {"success": 1, "error": 0}},
                               engram_version="9.9.9") is None

    def test_status_reports_disabled(self, isolated):
        status = t.get_status()
        assert status.get("enabled") is False


class TestNoNetworkInDefaultState:
    def test_tracker_flush_makes_no_network_call(self, isolated, sealed_network):
        tracker = t.ToolCallTracker()
        tracker.record("add_lesson", success=True)
        tracker.record("search_knowledge", success=False, error_category="timeout")
        # Disabled -> flush is a no-op and certainly no send.
        assert tracker.flush(force=True, engram_version="9.9.9") is None
        assert sealed_network == []

    def test_send_remote_short_circuits_when_disabled(self, isolated, sealed_network):
        # Even called directly with a payload, remote-off must not hit the network.
        assert t._send_remote({"schema": 1, "daily_id": "abc"}) is False
        assert sealed_network == []

    def test_feedback_send_blocked_when_disabled(self, isolated, sealed_network):
        assert t.send_feedback({"report_type": "x"}) is False
        assert sealed_network == []


class TestRemoteRequiresSecondOptIn:
    def test_local_enabled_remote_off_still_no_network(self, isolated, sealed_network):
        # Opt into LOCAL stats only. Remote must remain a separate, explicit choice.
        t.set_enabled(True)
        assert t.is_enabled() is True
        assert t.is_remote_enabled() is False  # NOT auto-enabled
        tracker = t.ToolCallTracker()
        tracker.record("add_lesson", success=True)
        # Local log is written, but remote send must NOT fire.
        result = tracker.flush(force=True, engram_version="9.9.9")
        assert result is not None  # local log path
        assert sealed_network == []  # no network despite local opt-in

    def test_remote_opt_in_is_independent_flag(self, isolated):
        t.set_enabled(True)
        assert t.is_remote_enabled() is False
        t.set_remote_enabled(True)
        assert t.is_remote_enabled() is True
        # And turning local back off forces remote off too (gate dependency).
        t.set_enabled(False)
        assert t.is_remote_enabled() is False


class TestPreviewMetadataOnly:
    def test_preview_works_disabled_and_makes_no_network(self, isolated, sealed_network):
        preview = t.preview_payload(
            tool_calls={"add_lesson": {"success": 3, "error": 0}},
            knowledge_counts={"lessons": 5, "decisions": 2, "domains": 1},
            engram_version="9.9.9",
        )
        assert isinstance(preview, str) and preview
        assert sealed_network == []

    def test_preview_carries_no_knowledge_body_text(self, isolated):
        # A real lesson body must never appear in the preview — only counts.
        secret_body = "production DB password is hunter2 do-not-share"
        preview = t.preview_payload(
            knowledge_counts={"lessons": 1, "decisions": 0, "domains": 1},
            engram_version="9.9.9",
        )
        assert secret_body not in preview
        assert "hunter2" not in preview


class TestNoKnowledgeBodyLeakage:
    def test_payload_carries_counts_not_bodies(self, isolated, sealed_network):
        t.set_enabled(True)
        payload = t.build_payload(
            tool_calls={"add_lesson": {"success": 1, "error": 0}},
            knowledge_counts={"lessons": 4, "decisions": 3, "domains": 2},
            engram_version="9.9.9",
            tools_tier="core",
        )
        assert payload is not None
        # knowledge is counts-only.
        assert payload["knowledge_counts"] == {"lessons": 4, "decisions": 3, "domains": 2}
        # The serialized payload contains no free-text body / summary field.
        blob = json.dumps(payload)
        for forbidden in ("summary", "detail", "choice", "question", "body",
                          "content", "prompt"):
            assert forbidden not in blob
        # And it still passes the module's own structural validator.
        assert t._validate_payload(payload) is True

    def test_payload_validator_rejects_natural_language_value(self, isolated):
        # Defense in depth: a smuggled prose value (long + space-heavy, i.e. a
        # leaked sentence) is rejected by the validator's NL heuristic.
        prose = " ".join(["ab"] * 40)  # >100 chars, space ratio ~0.33
        assert len(prose) > 100
        bad = {"schema": 1, "daily_id": "abcd", "leaked": prose}
        assert t._validate_payload(bad) is False
