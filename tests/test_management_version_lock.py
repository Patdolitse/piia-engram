"""Management writes check expected versions inside their commit locks."""

import hashlib

import portalocker
import pytest

from piia_engram.core import Engram


def _digest(eng):
    return {str(p.relative_to(eng.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for name in ("knowledge", "playbooks")
            for p in (eng.root / name).rglob("*") if p.is_file()}


@pytest.mark.parametrize("action", ["delete", "restore", "merge"])
def test_change_before_first_commit_lock_is_a_zero_write_conflict(tmp_path, monkeypatch, action):
    eng = Engram(root=tmp_path / "store")
    row = eng.add_playbook({"title": "Manage a deployment", "steps": [{"action": "verify"}]})
    secondary = None
    if action == "restore":
        eng.delete_playbook(row["id"], dry_run=False, confirm=True)
    elif action == "merge":
        secondary = eng.add_lesson("Record deployment prerequisites before running a release", tier="verified")
    version = eng._read_playbook_by_id(row["id"])["version"]
    real_acquire = portalocker.Lock.acquire
    state = {}

    def interleave(lock, *args, **kwargs):
        if not state.get("interleaved"):
            state["interleaved"] = True
            eng._update_playbook_file_by_id(row["id"], lambda current: {
                **current, "version": current["version"] + 1, "description": "concurrent edit"})
            state["after_edit"] = _digest(eng)
        return real_acquire(lock, *args, **kwargs)

    monkeypatch.setattr(portalocker.Lock, "acquire", interleave)
    if action == "merge":
        result = eng.merge_knowledge(row["id"], secondary["id"],
                                     primary_expected_version=version, secondary_expected_version=1)
    else:
        result = getattr(eng, action + "_playbook")(
            row["id"], dry_run=False, confirm=True, expected_version=version)
    assert state.get("interleaved"), "the test never reached a commit lock"
    assert result.get("error") == "version_conflict", result
    assert _digest(eng) == state["after_edit"]
