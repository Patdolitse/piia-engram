"""The Owner's review compares the reviewed version inside the lock that commits.

A row changed between the reviewer's version check and the commit (another
process editing it) is ``version_conflict`` and nothing is written: no
approval, no promotion, no archive and no tombstone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=root)


def _row(eng: Engram, item_id: str) -> dict:
    return dict(eng._find_item_by_id(item_id)[1] or {})


def _digest(root: Path) -> dict[str, str]:
    out = {}
    for sub in ("knowledge", "playbooks"):
        base = root / sub
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.name.startswith(".engram-write"):
                    out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _pending_playbook(eng: Engram) -> dict:
    pb = eng.add_playbook({"title": "Pending playbook under review", "steps": [{"action": "v1"}],
                           "tier": "staging"})
    eng._update_playbook_file_by_id(pb["id"], lambda r: {**r, "tier": "staging", "approval_status": "pending"})
    assert eng.is_pending_playbook(eng._read_playbook_by_id(pb["id"]))
    return pb


def _interleave(eng: Engram, monkeypatch, method: str, bump) -> None:
    """Another writer changes the row after the review's own check, before the commit."""
    real = getattr(eng, method)

    def _wrapped(item_id, *args, **kwargs):
        bump(item_id)
        return real(item_id, *args, **kwargs)

    monkeypatch.setattr(eng, method, _wrapped)


def _bump_lesson(eng: Engram):
    def bump(item_id):
        other = Engram(root=eng.root)
        assert other.update_knowledge(item_id, {"detail": "edited by another process"}).get("version") == 2
    return bump


def _bump_playbook(eng: Engram):
    def bump(item_id):
        other = Engram(root=eng.root)
        assert other.update_playbook(item_id, {"steps": [{"action": "v2 unreviewed"}]}).get("version") == 2
    return bump


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_lesson_changed_before_the_commit_is_a_conflict(eng, monkeypatch, action):
    lesson = eng.add_lesson({"summary": "Proposal the owner reviewed at version one", "tier": "staging"})
    method = "promote_knowledge" if action == "approve" else "archive_knowledge"
    _interleave(eng, monkeypatch, method, _bump_lesson(eng))
    out = batch_review_staging(eng, [{"id": lesson["id"], "action": action, "expected_version": 1}],
                               dry_run=False, confirm=True, owner_cli=True)
    assert out["items"][0]["status"] == "version_conflict", out
    row = _row(eng, lesson["id"])
    assert row["tier"] == "staging" and row["status"] == "active" and row["version"] == 2
    assert not (eng.root / "knowledge" / "tombstones.jsonl").exists()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_playbook_changed_before_the_commit_is_a_conflict(eng, monkeypatch, action):
    pb = _pending_playbook(eng)
    method = "approve_playbook" if action == "approve" else "reject_playbook"
    _interleave(eng, monkeypatch, method, _bump_playbook(eng))
    out = batch_review_staging(eng, [{"id": pb["id"], "action": action, "expected_version": 1}],
                               dry_run=False, confirm=True, owner_cli=True)
    assert out["items"][0]["status"] == "version_conflict", out
    row = eng._read_playbook_by_id(pb["id"])
    assert row["tier"] == "staging" and row["status"] == "active"
    assert not (eng.root / "knowledge" / "tombstones.jsonl").exists()


@pytest.mark.parametrize("call", ["promote", "approve_playbook", "reject_playbook", "archive_reject"])
def test_core_operations_compare_the_version_they_are_given(eng, call):
    if call in ("promote", "archive_reject"):
        row = eng.add_lesson({"summary": f"Core op lesson for {call}", "tier": "staging"})
        Engram(root=eng.root).update_knowledge(row["id"], {"detail": "now version two"})
    else:
        row = _pending_playbook(eng)
        Engram(root=eng.root).update_playbook(row["id"], {"steps": [{"action": "v2"}]})
    before = _digest(eng.root)
    if call == "promote":
        result = eng.promote_knowledge(row["id"], expected_version=1)
    elif call == "approve_playbook":
        result = eng.approve_playbook(row["id"], expected_version=1)
    elif call == "reject_playbook":
        result = eng.reject_playbook(row["id"], _owner_reject="cli:test", expected_version=1)
    else:
        result = eng.archive_knowledge(row["id"], _owner_reject="cli:test", expected_version=1)
    assert "version_conflict" in json.dumps(result), result
    assert _digest(eng.root) == before


def test_matching_version_still_applies(eng):
    lesson = eng.add_lesson({"summary": "Proposal approved at its current version", "tier": "staging"})
    pb = _pending_playbook(eng)
    out = batch_review_staging(eng, [{"id": lesson["id"], "action": "approve", "expected_version": 1},
                                     {"id": pb["id"], "action": "reject", "expected_version": 1}],
                               dry_run=False, confirm=True, owner_cli=True)
    assert [i["status"] for i in out["items"]] == ["applied", "applied"]
    assert _row(eng, lesson["id"])["tier"] == "verified"
    assert eng._read_playbook_by_id(pb["id"])["status"] != "active"
