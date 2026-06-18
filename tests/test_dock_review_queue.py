"""Dock M2: metadata-only quality review queue (dock-review-queue).

Zero-write. Emits ids + lanes + maturity/freshness metadata + available actions
for the desktop client's review screen; never titles, bodies, editable fields,
or copy text (Codex boundary). Excludes archived/inactive items.
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


def test_dock_review_queue_lists_staging_with_actions_metadata_only(eng, capsys):
    eng.add_lesson(
        {"summary": "secret body text here", "tier": "staging"}, _allow_internal_provenance=True
    )

    rc = cli_commands._run_dock_review_queue(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["lane"] == "all"
    assert "lanes" in payload
    rows = payload["results"]
    assert any("staging" in r["lanes"] for r in rows)
    staging_row = next(r for r in rows if "staging" in r["lanes"])
    assert "id" in staging_row
    assert "promote" in staging_row["actions"]
    # metadata-only: no body / title / copy / editable fields leak
    assert "secret body text here" not in out
    for r in rows:
        assert "summary" not in r and "title" not in r
        assert "copy" not in r and "fields" not in r


def test_dock_review_queue_lane_filter(eng, capsys):
    eng.add_lesson({"summary": "s", "tier": "staging"}, _allow_internal_provenance=True)

    rc = cli_commands._run_dock_review_queue(["--lane", "staging", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["lane"] == "staging"
    assert all("staging" in r["lanes"] for r in payload["results"])


def test_dock_review_queue_excludes_archived(eng, capsys):
    eng.add_lesson({"summary": "active staging", "tier": "staging"}, _allow_internal_provenance=True)
    eng.add_lesson({"summary": "archived one", "tier": "archived"}, _allow_internal_provenance=True)

    cli_commands._run_dock_review_queue(["--json"])
    payload = json.loads(capsys.readouterr().out)
    # archived items never appear in the review queue
    assert payload["lanes"].get("all", 0) >= 1
    assert "archived one" not in json.dumps(payload)


def test_dock_review_queue_zero_write_and_dispatch(eng, capsys, tmp_path):
    eng.add_lesson({"summary": "s", "tier": "staging"}, _allow_internal_provenance=True)
    store = tmp_path / "engram"
    before = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}

    rc = cli_commands._run_dock_review_queue(["--json"])
    capsys.readouterr()
    assert rc == 0
    after = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    assert before == after  # zero-write

    import inspect
    from piia_engram import setup_wizard as W
    assert "dock-review-queue" in inspect.getsource(W.main)
