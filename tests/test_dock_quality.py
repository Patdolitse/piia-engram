"""Dock M2: owner-confidence knowledge quality summary (dock-quality).

Zero-write, metadata-only aggregate the desktop client shows on its quality
screen: counts by tier / freshness / validation maturity + review-queue
pressure + the next owner-facing review lane. No ids / titles / bodies /
reasoning / raw session content leak (Codex boundary for the read surfaces).
"""
from __future__ import annotations

import json

import pytest

from piia_engram import setup_wizard  # noqa: F401 — resolve setup_wizard<->cli_commands cycle
from piia_engram import cli_commands
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path, monkeypatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


def test_dock_quality_json_aggregates_and_steers_next_action(eng, capsys):
    eng.add_lesson({"summary": "a verified fact", "tier": "verified"})
    eng.add_lesson(
        {"summary": "a staging candidate", "tier": "staging"}, _allow_internal_provenance=True
    )
    eng.add_decision({"question": "db choice", "choice": "postgres"})

    rc = cli_commands._run_dock_quality(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["dock_contract_version"] == "M1"
    q = payload["quality"]
    assert q["total"] >= 3
    assert q["tier"]["staging"] >= 1
    for block in ("by_kind", "tier", "freshness", "labeling", "review_queue"):
        assert block in q
    # a staging candidate present -> the owner's next lane is review_staging
    assert payload["next_action"] == "review_staging"
    # metadata-only: no raw bodies / titles leak into the summary
    assert "a staging candidate" not in out
    assert "a verified fact" not in out


def test_dock_quality_no_pressure_next_action_none(eng, capsys):
    eng.add_lesson({"summary": "clean fresh verified lesson", "tier": "verified"})

    rc = cli_commands._run_dock_quality(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    rq = payload["quality"]["review_queue"]
    assert rq["staging"] == 0
    assert payload["next_action"] == "none"


def test_dock_quality_is_zero_write(eng, capsys, tmp_path):
    eng.add_lesson({"summary": "x", "tier": "staging"}, _allow_internal_provenance=True)
    store = tmp_path / "engram"
    before = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}

    rc = cli_commands._run_dock_quality(["--json"])
    capsys.readouterr()
    assert rc == 0
    after = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    assert before == after  # zero-write: no store file added or mutated


def test_dock_quality_wired_into_cli_dispatch():
    # dock-quality must be a known command + on the zero-write read list.
    import inspect
    from piia_engram import setup_wizard as W

    src = inspect.getsource(W.main)
    assert '"dock-quality"' in src or "'dock-quality'" in src
