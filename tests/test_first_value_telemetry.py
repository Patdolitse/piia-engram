"""First-value funnel telemetry — foundation (gate + whitelist schema + local write).

Design (Claude+Codex+research synthesis): opt-in, content-blind, bucketed,
local-only MVP. The spine is record_first_value_event() guarded by:
  - opt-in (off by default), and DO_NOT_TRACK / NO_TELEMETRY / CI suppress it
  - a STRICT whitelist: unknown event, unknown field, or a value outside the
    field's allowed set -> reject the whole event (fail-closed, never write)
This is the privacy red-line: a stray content-shaped field can never leak.
"""
from __future__ import annotations

import pytest

from piia_engram import telemetry


@pytest.fixture()
def tele(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    for k in ("DO_NOT_TRACK", "NO_TELEMETRY", "CI", "GITHUB_ACTIONS", "ENGRAM_TELEMETRY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


# --- gate: opt-in + suppressors --------------------------------------------


def test_off_by_default_no_write(tele):
    wrote = telemetry.record_first_value_event(
        "onboard.scan.completed", {"outcome": "success"}
    )
    assert wrote is False
    assert not telemetry.first_value_log_path().exists()


def test_enabled_writes_valid_event(tele, monkeypatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    wrote = telemetry.record_first_value_event(
        "onboard.scan.completed",
        {"anchors_bucket": "5_9", "repo_identity": "resolved", "outcome": "success",
         "error_category": "none"},
        surface="cli", client_tool="claude_code",
    )
    assert wrote is True
    events = telemetry.read_first_value_events()
    assert events and events[-1]["event"] == "onboard.scan.completed"
    assert events[-1]["fields"]["outcome"] == "success"
    assert events[-1]["surface"] == "cli"
    # never write a persistent identifier into the funnel log
    assert "local_uuid" not in events[-1]
    assert "uuid" not in str(events[-1]).lower()


@pytest.mark.parametrize("var", ["DO_NOT_TRACK", "NO_TELEMETRY", "CI", "GITHUB_ACTIONS"])
def test_suppressors_block_write_even_when_enabled(tele, monkeypatch, var):
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    monkeypatch.setenv(var, "1" if var in ("DO_NOT_TRACK", "NO_TELEMETRY") else "true")
    wrote = telemetry.record_first_value_event(
        "onboard.scan.completed", {"outcome": "success"}
    )
    assert wrote is False
    assert not telemetry.first_value_log_path().exists()


# --- strict whitelist (fail-closed) ----------------------------------------


def test_rejects_unknown_event(tele, monkeypatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    assert telemetry.record_first_value_event("totally.made.up", {"x": "y"}) is False
    assert not telemetry.first_value_log_path().exists()


def test_rejects_unknown_or_content_field(tele, monkeypatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    # a content-shaped field (raw query / path) is simply not on the whitelist
    assert telemetry.record_first_value_event(
        "onboard.scan.completed", {"query": "my secret project name"}
    ) is False
    assert telemetry.record_first_value_event(
        "onboard.scan.completed", {"repo_path": "/Users/alice/work/secret"}
    ) is False
    assert not telemetry.first_value_log_path().exists()


def test_rejects_value_outside_enum(tele, monkeypatch):
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    # a known field but a content value (not in the allowed bucket/enum) is rejected
    assert telemetry.record_first_value_event(
        "onboard.scan.completed", {"outcome": "/Users/alice/secret"}
    ) is False
    assert not telemetry.first_value_log_path().exists()


def test_known_events_have_bucketed_or_enum_schema():
    # every whitelisted field must allow only bool or a closed string set —
    # never a free string (which could carry content).
    for event, schema in telemetry.FIRST_VALUE_SCHEMA.items():
        assert schema, f"{event} has empty schema"
        for field, allowed in schema.items():
            assert allowed in (bool,) or isinstance(allowed, (set, frozenset)), (
                f"{event}.{field} must be bool or a closed set, got {allowed!r}"
            )
