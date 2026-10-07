"""A failed index write must preserve an existing playbook exactly."""

import pytest

from piia_engram.core import Engram


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_index_failure_restores_existing_body(tmp_path, monkeypatch, action):
    eng = Engram(root=tmp_path / "store")
    row = eng.add_playbook({"title": "Recover a deployment", "steps": [{"action": "check"}]})
    if action == "restore":
        eng.delete_playbook(row["id"], dry_run=False, confirm=True)
    path = eng._playbook_path(row["id"])
    index = eng._playbooks_dir / "_index.json"
    before_body, before_index = path.read_bytes(), index.read_bytes()

    def fail(mutator):
        raise OSError("synthetic index failure")

    monkeypatch.setattr(eng, "_update_playbook_index", fail)
    with pytest.raises(OSError, match="synthetic index failure"):
        getattr(eng, action + "_playbook")(row["id"], dry_run=False, confirm=True)
    assert path.exists(), "an existing body was removed after an index failure"
    assert path.read_bytes() == before_body
    assert index.read_bytes() == before_index
