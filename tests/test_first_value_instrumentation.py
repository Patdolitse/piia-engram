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
