"""An interrupted approval of a playbook revision is completed by applying the
same review marks again.

Approval retires the replaced playbook first, then approves the revision; a
run stopped between the two leaves no second usable version, and re-running
the marks finishes it. A store already left with both approved (an earlier
interrupted run) is completed the same way: the old one is retired and its
pin removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import pinning, review_cli
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=root)


def _revision(eng: Engram, title: str) -> tuple[dict, dict]:
    old = eng.add_playbook({"title": title, "steps": [{"action": "old"}]})
    new = eng.add_playbook({"title": title + " (revised)", "steps": [{"action": "new"}]},
                           _update_proposal_of=old["id"], allow_similar_new=True)
    assert eng.is_pending_playbook(eng._read_playbook_by_id(new["id"]))
    return old, new


def _apply(tmp_path: Path, new_id: str, mark: str = "approve", **guards) -> int:
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": new_id, "mark": mark, **guards}]), encoding="utf-8")
    return review_cli.run_apply([str(marks), "--operator", "owner", "--yes"])


def _usable(eng: Engram, pb_id: str) -> bool:
    row = eng._read_playbook_by_id(pb_id)
    return row.get("status") == "active" and row.get("tier") == "verified"


def test_interrupted_approval_is_completed_by_the_same_marks(eng, tmp_path, monkeypatch):
    old, new = _revision(eng, "Deploy the docs site")
    real = Engram._update_playbook_file_by_id
    calls: list[str] = []

    def _flaky(self, playbook_id, mutator):
        calls.append(playbook_id)
        if len({c for c in calls if c in (old["id"], new["id"])}) == 2 and not getattr(_flaky, "fired", False):
            _flaky.fired = True  # the second of the two writes is interrupted
            raise OSError("interrupted")
        return real(self, playbook_id, mutator)

    monkeypatch.setattr(Engram, "_update_playbook_file_by_id", _flaky)
    try:
        _apply(tmp_path, new["id"])
    except OSError:
        pass
    assert getattr(_flaky, "fired", False)
    # never two usable versions after the interruption
    assert not (_usable(eng, old["id"]) and _usable(eng, new["id"]))
    monkeypatch.setattr(Engram, "_update_playbook_file_by_id", real)

    assert _apply(tmp_path, new["id"]) == 0
    assert _usable(eng, new["id"]) and eng._read_playbook_by_id(old["id"])["status"] != "active"


@pytest.mark.parametrize("explicit", [False, True])
def test_a_store_left_with_both_approved_is_completed(eng, tmp_path, explicit):
    old, new = _revision(eng, "Rotate the database password")
    assert pinning.pin(eng, old["id"]).get("status") in ("pinned", "already_pinned")
    # the state an interrupted earlier approval left behind: both approved and active
    eng._update_playbook_file_by_id(new["id"], lambda r: {**r, "tier": "verified",
                                                          "approval_status": "approved",
                                                          "promotion_reason": "owner_review"})
    assert _usable(eng, old["id"]) and _usable(eng, new["id"])

    mark = "supersede:" + old["id"] if explicit else "approve"
    assert _apply(tmp_path, new["id"], mark) == 0
    assert _usable(eng, new["id"])
    retired = eng._read_playbook_by_id(old["id"])
    assert retired["status"] != "active" and not pinning.is_pinned(retired)
    # applying the marks once more changes nothing
    before = eng._read_playbook_by_id(old["id"])
    assert _apply(tmp_path, new["id"], mark) in (0, 1)
    assert eng._read_playbook_by_id(old["id"]) == before
    assert _usable(eng, new["id"]) and eng._read_playbook_by_id(old["id"])["status"] != "active"


@pytest.mark.parametrize("mode", ["preview", "stale", "wrong_target"])
def test_supersede_recovery_keeps_guards_and_preview_read_only(eng, tmp_path, mode):
    old, new = _revision(eng, "Review replacement guards")
    other = eng.add_playbook({"title": "Unrelated checklist", "steps": [{"action": "other"}]})
    eng._update_playbook_file_by_id(new["id"], lambda r: {
        **r, "tier": "verified", "approval_status": "approved", "promotion_reason": "owner_review"})
    ids = [old["id"], new["id"], other["id"]]
    before = [eng._playbook_path(i).read_bytes() for i in ids]
    target = other["id"] if mode == "wrong_target" else old["id"]
    mark = {"id": new["id"], "mark": "supersede", "target": target}
    if mode == "stale":
        mark["expected_version"] = 99
    result = review_cli._review_one(eng, mark, review_cli._new_counts(), dry_run=mode == "preview", via="test")
    assert result["status"] == {"preview": "planned", "stale": "version_conflict", "wrong_target": "not_staging"}[mode]
    assert [eng._playbook_path(i).read_bytes() for i in ids] == before


def test_supersede_recovery_rechecks_the_target_under_commit_locks(eng, monkeypatch):
    from contextlib import contextmanager

    old, new = _revision(eng, "Recover the reviewed target")
    other = eng.add_playbook({"title": "Another approved checklist", "steps": [{"action": "other"}]})
    eng._update_playbook_file_by_id(new["id"], lambda r: {
        **r, "tier": "verified", "approval_status": "approved", "promotion_reason": "owner_review"})
    real_locks = eng._review_locks
    snapshots = []

    @contextmanager
    def concurrent_target_change():
        with real_locks():
            if not snapshots:
                eng._update_playbook_file_by_id(new["id"], lambda r: {**r, "pending_supersedes": other["id"]})
                snapshots.extend(eng._playbook_path(i).read_bytes() for i in (old["id"], new["id"], other["id"]))
            yield

    monkeypatch.setattr(eng, "_review_locks", concurrent_target_change)
    result = review_cli._review_one(eng, {"id": new["id"], "mark": "supersede", "target": old["id"]},
                                   review_cli._new_counts(), dry_run=False, via="test")
    assert result["status"] == "not_staging"
    assert [eng._playbook_path(i).read_bytes() for i in (old["id"], new["id"], other["id"])] == snapshots
