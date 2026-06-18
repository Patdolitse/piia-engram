"""First-value funnel instrumentation — the onboard funnel (scan -> candidates -> accept).

Result-extractor pattern: each core function, after its business logic, derives
a bucketed outcome from its RESULT (never its args) and records one funnel
event. These integration tests drive the real Engram flow and assert the funnel
chain is recorded with valid bucketed values — and nothing when telemetry is off.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram import telemetry
from piia_engram.core import Engram

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "onboard_repo_golden"


@pytest.fixture()
def fv_on(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    for k in ("DO_NOT_TRACK", "NO_TELEMETRY", "CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda r: "github.com/acme/app"
    )
    return root


def test_onboard_funnel_records_scan_candidates_accept(fv_on):
    eng = Engram(root=fv_on)
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    eng.accept_onboard_candidates(project_root=str(GOLDEN))

    events = telemetry.read_first_value_events()
    names = [e["event"] for e in events]
    assert "onboard.scan.completed" in names
    assert "onboard.candidates.materialized" in names
    assert "onboard.accept.batch_completed" in names

    scan = next(e for e in events if e["event"] == "onboard.scan.completed")
    assert scan["fields"]["repo_identity"] == "resolved"
    assert scan["fields"]["outcome"] == "success"
    assert scan["fields"]["anchors_bucket"] in telemetry.FIRST_VALUE_SCHEMA[
        "onboard.scan.completed"]["anchors_bucket"]

    mat = next(e for e in events if e["event"] == "onboard.candidates.materialized")
    assert mat["fields"]["created_bucket"] in telemetry.FIRST_VALUE_SCHEMA[
        "onboard.candidates.materialized"]["created_bucket"]
    assert mat["fields"]["candidate_mix"] in {"none", "dep_only", "file_only", "mixed"}

    batch = next(e for e in events if e["event"] == "onboard.accept.batch_completed")
    assert batch["fields"]["acceptance_rate"] in {"none", "low", "medium", "high", "all"}
    assert isinstance(batch["fields"]["dry_run"], bool)
    # the 385/0 question: some accepts actually happened on the golden repo
    assert batch["fields"]["accepted_bucket"] != "0"

    # privacy: no raw content / path / repo id ever appears in the funnel log
    blob = "\n".join(str(e) for e in events)
    assert "acme/app" not in blob
    assert "react" not in blob
    assert str(GOLDEN) not in blob


def test_onboard_funnel_dry_run_marked(fv_on):
    eng = Engram(root=fv_on)
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    eng.accept_onboard_candidates(project_root=str(GOLDEN), dry_run=True)

    batch = next(
        e for e in telemetry.read_first_value_events()
        if e["event"] == "onboard.accept.batch_completed"
    )
    assert batch["fields"]["dry_run"] is True


def test_onboard_funnel_off_records_nothing(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_TELEMETRY", "0")
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda r: "github.com/acme/app"
    )
    eng = Engram(root=root)
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    eng.accept_onboard_candidates(project_root=str(GOLDEN))
    assert telemetry.read_first_value_events() == []
    assert not telemetry.first_value_log_path().exists()


def test_recall_funnel_records_trust_and_cross_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    for k in ("DO_NOT_TRACK", "NO_TELEMETRY", "CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(k, raising=False)
    from piia_engram import recall_service

    payload = {
        "knowledge": [
            {  # written by claude_code, recalled in codex -> cross-tool, trusted
                "summary": "depends on react",
                "trust": {
                    "confirmation_source": "anchor", "anchor": "dep:react",
                    "anchor_status": "valid", "validated_at": "2026-06-18T10:00:00",
                    "decay_policy": "trigger",
                },
                "provenance": {"source_agent": "claude_code"},
            },
            {"summary": "y", "provenance": {"source_agent": "codex"}},  # same tool
        ]
    }
    recall_service.record_recall_funnel(payload, current_tool="codex", surface="mcp")

    events = telemetry.read_first_value_events()
    names = [e["event"] for e in events]
    assert "recall.trust.payoff" in names
    assert "recall.cross_tool.payoff" in names

    trust = next(e for e in events if e["event"] == "recall.trust.payoff")
    assert trust["fields"]["payoff"] is True
    assert trust["fields"]["trust_basis"] == "anchor"
    assert trust["fields"]["anchor_status_mix"] == "valid_only"
    assert trust["fields"]["has_validated_at"] is True

    ct = next(e for e in events if e["event"] == "recall.cross_tool.payoff")
    assert ct["fields"]["current_tool"] == "codex"
    assert ct["fields"]["source_relation"] == "mixed"  # one cross, one same
    assert ct["fields"]["payoff"] is True

    # privacy red line: the tool PAIR and the anchor/content never appear
    blob = "\n".join(str(e) for e in events)
    assert "claude_code" not in blob      # the OTHER tool is never recorded
    assert "dep:react" not in blob
    assert "depends on react" not in blob


def test_telemetry_funnel_command_shows_stages(fv_on, capsys):
    from piia_engram import setup_wizard  # noqa: F401 — resolve import cycle
    from piia_engram import cli_commands

    eng = Engram(root=fv_on)
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    eng.accept_onboard_candidates(project_root=str(GOLDEN))

    cli_commands._run_telemetry_cli(["funnel"])
    out = capsys.readouterr().out
    assert "First value funnel" in out
    assert "scan:" in out
    assert "accepted:" in out
    assert "trusted recall:" in out
    assert "cross-tool" in out
    assert "dropoff:" in out
    # privacy: no fact content / repo path ever appears in the funnel view
    assert "react" not in out
    assert str(GOLDEN) not in out


def test_telemetry_funnel_command_empty_when_no_events(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
    monkeypatch.setenv("ENGRAM_TELEMETRY", "0")
    from piia_engram import setup_wizard  # noqa: F401
    from piia_engram import cli_commands

    cli_commands._run_telemetry_cli(["funnel"])
    out = capsys.readouterr().out
    assert "First value funnel" in out
    assert "no" in out.lower()  # nothing reached


def test_onboard_funnel_respects_do_not_track(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    monkeypatch.setenv("DO_NOT_TRACK", "1")  # owner's real setup
    monkeypatch.setattr(
        "piia_engram.freshness_anchors.read_project_id", lambda r: "github.com/acme/app"
    )
    eng = Engram(root=root)
    eng.onboard_repo(str(GOLDEN), repo_id="github.com/acme/app")
    assert telemetry.read_first_value_events() == []
