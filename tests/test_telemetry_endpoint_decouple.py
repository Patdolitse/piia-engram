"""Tests for telemetry endpoint decoupling from any personal handle (M0 G4 / #80).

The open-source core must ship with NO built-in telemetry destination and no
personal domain/handle baked into source. Remote send is enabled only when an
operator explicitly sets ENGRAM_TELEMETRY_URL / ENGRAM_FEEDBACK_URL; when unset
the send path is a no-op that performs no outbound request.

All tests isolate ENGRAM_DIR to a temporary directory and mock the network so
nothing touches a real store or the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram import telemetry


@pytest.fixture(autouse=True)
def _isolate_engram_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point Engram data dir at a temp dir; never touch the real store."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_TELEMETRY_URL", raising=False)
    monkeypatch.delenv("ENGRAM_FEEDBACK_URL", raising=False)
    yield


def test_default_endpoints_are_empty_and_carry_no_personal_handle():
    """No hardcoded default destination; no personal handle in the constants."""
    assert telemetry._DEFAULT_ENDPOINT == ""
    assert telemetry._DEFAULT_FEEDBACK_ENDPOINT == ""
    assert "pp3x325" not in telemetry._DEFAULT_ENDPOINT
    assert "pp3x325" not in telemetry._DEFAULT_FEEDBACK_ENDPOINT


def test_telemetry_source_has_no_personal_handle():
    """The shipped telemetry module source must not embed a personal handle."""
    src = Path(telemetry.__file__).read_text(encoding="utf-8")
    assert "pp3x325" not in src


def test_get_endpoint_empty_when_env_unset():
    assert telemetry.get_endpoint() == ""


def test_get_endpoint_uses_env_when_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", "https://my-relay.example.test/v1/events")
    assert telemetry.get_endpoint() == "https://my-relay.example.test/v1/events"


def test_send_remote_is_noop_without_endpoint(monkeypatch: pytest.MonkeyPatch):
    """Even when remote is opted in, an empty endpoint => no network call."""
    monkeypatch.setattr(telemetry, "is_remote_enabled", lambda: True)

    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("urlopen must not be called when endpoint is empty")

    monkeypatch.setattr(telemetry, "urlopen", _boom)

    assert telemetry._send_remote({"event": "noop"}) is False


def test_send_remote_attempts_request_when_endpoint_set(monkeypatch: pytest.MonkeyPatch):
    """With an explicit endpoint configured, the send path builds a request."""
    monkeypatch.setattr(telemetry, "is_remote_enabled", lambda: True)
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", "https://my-relay.example.test/v1/events")

    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(telemetry, "urlopen", _fake_urlopen)

    assert telemetry._send_remote({"event": "ping"}) is True
    assert captured["url"] == "https://my-relay.example.test/v1/events"


def test_send_feedback_is_noop_without_endpoint(monkeypatch: pytest.MonkeyPatch):
    """Empty feedback endpoint => send_feedback never reaches the network."""
    monkeypatch.setattr(telemetry, "is_feedback_enabled", lambda: True)

    # Seed a local_uuid so the function passes its pre-network checks and would
    # otherwise reach the endpoint resolution / send step.
    telemetry._save_config({"local_uuid": "test-uuid-0000"})

    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("urlopen must not be called when feedback endpoint is empty")

    monkeypatch.setattr(telemetry, "urlopen", _boom)

    # A minimal report that PASSES the send-boundary allowlist (only allowed
    # keys, coarse metadata values) so the function reaches endpoint resolution
    # and we genuinely exercise the empty-endpoint guard.
    report = {"report_type": "weekly", "report_version": "1"}
    assert telemetry.send_feedback(report) is False
