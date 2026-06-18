"""Dock M2: owner-confirmed quality action (dock-quality-action) — the ONLY write surface.

Boundaries (the ones Codex must scrutinize):
  - writes ONLY after --yes; without it -> dry-run, requires_confirmation, zero side effects
  - only validate / promote / archive (anything else -> invalid_action)
  - promote only acts on staging (verified/other -> item_not_staging, no change)
  - archive is a RECOVERABLE tier archive (not delete / not irreversible)
  - receipt is metadata-only (no titles / bodies / copy)
  - it is an owner-write action, NOT on the zero-write guard list
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


def _add_staging(eng: Engram) -> str:
    eng.add_lesson(
        {"summary": "secret staging body", "tier": "staging"}, _allow_internal_provenance=True
    )
    return next(
        e["id"] for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("tier") == "staging"
    )


def _tier_of(eng: Engram, item_id: str) -> str:
    _kind, item = eng._find_item_by_id(item_id)
    return str(item.get("tier")) if isinstance(item, dict) else "<gone>"


def test_quality_action_requires_yes_is_zero_write(eng, capsys):
    item_id = _add_staging(eng)
    rc = cli_commands._run_dock_quality_action(
        ["--action", "promote", "--id", item_id, "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert payload["ok"] is False
    assert payload["requires_confirmation"] is True
    assert payload["dry_run"] is True
    assert _tier_of(eng, item_id) == "staging"  # nothing written without --yes


def test_quality_action_promote_staging_with_yes(eng, capsys):
    item_id = _add_staging(eng)
    rc = cli_commands._run_dock_quality_action(
        ["--action", "promote", "--id", item_id, "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["result"]["to_tier"] == "verified"
    assert _tier_of(eng, item_id) == "verified"
    assert "secret staging body" not in json.dumps(payload)  # metadata-only receipt


def test_quality_action_promote_non_staging_refused_is_zero_write(eng, capsys, tmp_path):
    eng.add_lesson({"summary": "already verified", "tier": "verified"})
    vid = next(
        e["id"] for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("tier") == "verified"
    )
    store = tmp_path / "engram"
    before = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}

    rc = cli_commands._run_dock_quality_action(
        ["--action", "promote", "--id", vid, "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "item_not_staging"
    assert _tier_of(eng, vid) == "verified"  # unchanged
    # a refusal must stay zero-write: no session_state stamp / migration rewrite
    after = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    assert before == after


def test_quality_action_item_not_found_is_zero_write(eng, capsys, tmp_path):
    store = tmp_path / "engram"
    before = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    rc = cli_commands._run_dock_quality_action(
        ["--action", "validate", "--id", "nope-no-such-id", "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "item_not_found"
    after = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    assert before == after  # refusal stays zero-write


def test_promote_knowledge_require_tier_refuses_non_staging_atomically(eng):
    eng.add_lesson({"summary": "v", "tier": "verified"})
    vid = next(
        e["id"] for e in eng.get_lessons(limit=None, _update_access=False)
        if e.get("tier") == "verified"
    )
    res = eng.promote_knowledge(vid, require_tier="staging")
    assert res["status"] == "tier_mismatch"
    assert _tier_of(eng, vid) == "verified"  # locked mutator left it unchanged
    # the default (no require_tier) path still promotes, unchanged behavior
    res2 = eng.promote_knowledge(vid)
    assert res2["status"] == "promoted"


def test_quality_action_archive_is_recoverable(eng, capsys):
    item_id = _add_staging(eng)
    rc = cli_commands._run_dock_quality_action(
        ["--action", "archive", "--id", item_id, "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["result"]["reversible"] is True
    assert _tier_of(eng, item_id) == "archived"


def test_quality_action_validate_with_yes(eng, capsys):
    item_id = _add_staging(eng)
    rc = cli_commands._run_dock_quality_action(
        ["--action", "validate", "--id", item_id, "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["result"]["action"] == "validate"


def test_quality_action_invalid_action_refused(eng, capsys):
    rc = cli_commands._run_dock_quality_action(
        ["--action", "delete", "--id", "whatever", "--yes", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_action"


def test_quality_action_wired_and_classified_as_owner_write():
    import inspect
    from piia_engram import setup_wizard as W

    assert "dock-quality-action" in inspect.getsource(W.main)
    assert "dock-quality-action" in cli_commands._DOCK_OWNER_WRITE_ACTIONS
    assert "dock-quality-action" not in cli_commands._DOCK_ZERO_WRITE_ACTIONS
