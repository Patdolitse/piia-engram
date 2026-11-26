"""Tests for telemetry endpoints supplied only through explicit configuration.

The open-source core must ship with NO built-in telemetry destination and no
personal domain/handle baked into source. Remote send is enabled only when an
operator explicitly sets ENGRAM_TELEMETRY_URL / ENGRAM_FEEDBACK_URL; when unset
the send path is a no-op that performs no outbound request.

All tests isolate ENGRAM_DIR to a temporary directory and mock the network so
nothing touches a real store or the network.
"""

from __future__ import annotations

import ast
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


COUNTS_URL = "https://telemetry.piia-engram.com/v1/counts"


def test_default_endpoints_carry_no_personal_handle():
    """The daily usage counts go to the project's telemetry host; weekly
    feedback has no built-in destination at all."""
    assert telemetry._DEFAULT_ENDPOINT == COUNTS_URL
    assert telemetry._DEFAULT_FEEDBACK_ENDPOINT == ""


def test_telemetry_source_has_exactly_one_builtin_destination():
    """The only URL in the module is the counts endpoint constant."""
    src = Path(telemetry.__file__).read_text(encoding="utf-8")
    literals = [
        node.value for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith(("https://", "http://"))
    ]
    assert literals == [COUNTS_URL]


def test_get_endpoint_is_the_default_when_env_unset():
    assert telemetry.get_endpoint() == COUNTS_URL


def test_get_endpoint_uses_env_when_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", "https://my-relay.example.test/v1/events")
    assert telemetry.get_endpoint() == "https://my-relay.example.test/v1/events"


def test_the_module_has_no_synchronous_sender_for_the_counts(monkeypatch: pytest.MonkeyPatch):
    """Sending the counts is usage_stats' job (background thread); nothing in
    this module posts to the counts endpoint."""
    assert not hasattr(telemetry, "_send_remote")
    src = Path(telemetry.__file__).read_text(encoding="utf-8")
    assert src.count("urlopen(") == 1  # the opt-in weekly feedback only
    assert "urlopen(" in src.split("def send_feedback", 1)[1]


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
