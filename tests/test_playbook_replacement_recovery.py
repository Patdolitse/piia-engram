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


def _apply(tmp_path: Path, new_id: str) -> int:
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": new_id, "mark": "approve"}]), encoding="utf-8")
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


def test_a_store_left_with_both_approved_is_completed(eng, tmp_path):
    old, new = _revision(eng, "Rotate the database password")
    assert pinning.pin(eng, old["id"]).get("status") in ("pinned", "already_pinned")
    # the state an interrupted earlier approval left behind: both approved and active
    eng._update_playbook_file_by_id(new["id"], lambda r: {**r, "tier": "verified",
                                                          "approval_status": "approved",
                                                          "promotion_reason": "owner_review"})
    assert _usable(eng, old["id"]) and _usable(eng, new["id"])

    assert _apply(tmp_path, new["id"]) == 0
    assert _usable(eng, new["id"])
    retired = eng._read_playbook_by_id(old["id"])
    assert retired["status"] != "active" and not pinning.is_pinned(retired)
    # applying the marks once more changes nothing
    assert _apply(tmp_path, new["id"]) in (0, 1)
    assert _usable(eng, new["id"]) and eng._read_playbook_by_id(old["id"])["status"] != "active"
