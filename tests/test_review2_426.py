"""Regression witnesses for conservative repair and physical version activity."""

import json

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    CONFIG_ENV, LIMITS_FILE, MARKER, REPLAY_MARKER_FILE, Config,
    GuardRefused, IsolatedStore, init_root,
)
from piia_engram.isolated_store_launch import build_child_env
from piia_engram.replay_row_repair import repair
from test_isolated_store import RESTORE, RETIRE, _make_world, _snap
from test_isolated_supersede import admit
from test_replay_experience import _admit, _world


def test_nested_external_ledger_must_refuse_repair(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, init=False)
    w.data.update(mode="replay_experience", receipts_dir=str(w.area / "ledgers" / "custom"))
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    w.cfg = Config.load(w.cfg_path)
    for key, value in build_child_env({}, w.cfg).items():
        monkeypatch.setenv(key, value)
    w.pr = init_root(w.cfg)
    assert _admit(w)["result"] == "admitted"
    for name in (MARKER, LIMITS_FILE, REPLAY_MARKER_FILE):
        (w.pr.root / name).unlink()
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    with pytest.raises(GuardRefused):
        Engram(root=w.pr.root, read_only=True)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="repair_replay_dominant"):
        repair(w.pr.root, apply=True)
    assert _snap(w.area) == before
    with pytest.raises(GuardRefused):
        Engram(root=w.pr.root, read_only=True)


def test_all_store_local_snapshot_extensions_are_invalidated(tmp_path):
    root = tmp_path / "ordinary"
    eng = Engram(root=root)
    eng.add_lesson("CUSTOM_SNAPSHOT_CANARY synthetic fixture", source_tool="cli")
    path = root / "cold-start.txt"
    eng.refresh_quick_context(target=path)
    assert "CUSTOM_SNAPSHOT_CANARY" in path.read_text(encoding="utf-8")
    knowledge = root / "knowledge" / "lessons.json"
    rows = json.loads(knowledge.read_text(encoding="utf-8"))
    rows[0]["store_mode"] = "replay_experience"
    # Eligible ordinary store: only one of ten rows is accidentally marked.
    rows.extend({"id": f"clean-{n}", "summary": "Synthetic clean fixture"} for n in range(9))
    knowledge.write_text(json.dumps(rows), encoding="utf-8")
    assert repair(root, apply=True)["changed"]
    assert len(Engram(root=root, read_only=True).get_lessons()) == 9
    assert not path.exists(), "generated snapshot survives repair with quarantined content"


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_crashed_restore_does_not_allow_two_active_versions(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    new = admit(w, 2, supersedes=old["item_id"])
    assert w.pr.retire(new["item_id"], "round", RETIRE)["result"] == "retired"
    original_append = w.pr._append

    def crash(record):
        if record.get("op") == "restore" and record.get("result") == "restored":
            raise SystemExit("crash after physical restore before receipt")
        return original_append(record)

    with monkeypatch.context() as patch:
        patch.setattr(w.pr, "_append", crash)
        with pytest.raises(SystemExit):
            w.pr.restore(old["item_id"], "round", RESTORE)
    w.pr = IsolatedStore.open(w.cfg)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersede_state_mismatch"):
        w.pr.restore(new["item_id"], "round", RESTORE)
    assert _snap(w.area) == before
    active = [r["id"] for r in w.pr._rows(w.pr._engram(read_only=True))
              if r.get("status") == "active"]
    assert active == [old["item_id"]]
    assert any(p.startswith("supersede_state_mismatch:") for p in w.pr.reconcile()["problems"])
