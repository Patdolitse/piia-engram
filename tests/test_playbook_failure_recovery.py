"""A failed index write must preserve an existing playbook exactly."""

import os

import pytest

from piia_engram.core import Engram


@pytest.mark.parametrize("action", ["delete", "restore"])
@pytest.mark.parametrize("short_write", [False, True])
def test_index_failure_restores_existing_body(tmp_path, monkeypatch, action, short_write):
    eng = Engram(root=tmp_path / "store")
    row = eng.add_playbook({"title": "Recover a deployment", "steps": [{"action": "check"}]})
    if action == "restore":
        eng.delete_playbook(row["id"], dry_run=False, confirm=True)
    path = eng._playbook_path(row["id"])
    index = eng._playbooks_dir / "_index.json"
    before_body, before_index = path.read_bytes(), index.read_bytes()
    if short_write:
        real_write = os.write
        monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:3]))

    def fail(mutator):
        raise OSError("synthetic index failure")

    monkeypatch.setattr(eng, "_update_playbook_index", fail)
    with pytest.raises(OSError, match="synthetic index failure"):
        getattr(eng, action + "_playbook")(row["id"], dry_run=False, confirm=True)
    assert path.exists(), "an existing body was removed after an index failure"
    assert path.read_bytes() == before_body
    assert index.read_bytes() == before_index


def test_atomic_bytes_short_writes_are_completed_before_replace(tmp_path, monkeypatch):
    from piia_engram import atomic_replace

    path = tmp_path / "body.json"
    path.write_bytes(b"original")
    data = b'{"id":"sample","title":"complete body"}'
    real_write, real_replace = os.write, atomic_replace.replace_with_retry
    calls = []

    def short_write(fd, chunk):
        calls.append(len(chunk))
        return real_write(fd, chunk[:3])

    def replace(source, destination):
        from pathlib import Path
        assert Path(source).read_bytes() == data
        assert path.read_bytes() == b"original"
        return real_replace(source, destination)

    monkeypatch.setattr(os, "write", short_write)
    monkeypatch.setattr(atomic_replace, "replace_with_retry", replace)
    Engram._atomic_write_bytes(path, data)
    assert path.read_bytes() == data and len(calls) > 1


@pytest.mark.parametrize("progress", [0, 3])
def test_atomic_bytes_zero_progress_preserves_destination(tmp_path, monkeypatch, progress):
    path = tmp_path / "body.json"
    path.write_bytes(b"original")
    real_write = os.write
    calls = 0

    def stop_write(fd, chunk):
        nonlocal calls
        calls += 1
        return real_write(fd, chunk[:progress]) if calls == 1 and progress else 0

    monkeypatch.setattr(os, "write", stop_write)
    with pytest.raises(OSError, match="write"):
        Engram._atomic_write_bytes(path, b"replacement")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.glob("*.tmp")) == []
