"""Backup inserts cannot overwrite existing bodies or internal files."""

import json
import os

import pytest

from piia_engram import pinning
from piia_engram.core import Engram
from piia_engram.playbooks import PlaybookIdExists, valid_playbook_id


def _backup(tmp_path, *rows):
    path = tmp_path / "backup.json"
    path.write_text(json.dumps({"schema_version": "1.0", "knowledge": {"playbooks": list(rows)}}), encoding="utf-8")
    return str(path)


def _row(eng, item_id, title):
    return eng._ensure_playbook_fields({"id": item_id, "title": title, "steps": [{"action": "check"}]})


def _files(eng):
    return {p.name: p.read_bytes() for p in eng._playbooks_dir.glob("*.json")}


@pytest.mark.parametrize("reserved", ["_index", "_INDEX"])
def test_internal_playbook_ids_are_reserved(reserved):
    assert not valid_playbook_id(reserved)


@pytest.mark.parametrize("reserved", ["_index", "_INDEX"])
def test_reserved_import_is_given_a_fresh_id(tmp_path, reserved):
    eng = Engram(root=tmp_path / "store")
    eng._write_playbook_and_index(_row(eng, "existing", "Existing checklist"), create=True)
    internal = eng._playbooks_dir / "_internal.json"
    internal.write_bytes(b"internal marker")
    before_body = eng._playbook_path("existing").read_bytes()
    result = eng.import_all(_backup(tmp_path, _row(eng, reserved, "Imported checklist")))
    assert "error" not in result
    assert eng._playbook_path("existing").read_bytes() == before_body
    assert internal.read_bytes() == b"internal marker"
    index = eng._read_playbook_index()
    assert len(index) == 2 and all(valid_playbook_id(r["id"]) for r in index)


@pytest.mark.parametrize("incoming_id", ["_index", "abc", "ABC"])
def test_import_failure_does_not_remove_existing_files(tmp_path, monkeypatch, incoming_id):
    eng = Engram(root=tmp_path / "store")
    eng._write_playbook_and_index(_row(eng, "abc", "Existing checklist"), create=True)
    before = _files(eng)

    def fail(*args):
        raise OSError("synthetic index failure")

    monkeypatch.setattr(eng, "_write_playbook_index", fail)
    monkeypatch.setattr(eng, "_update_playbook_index", fail)
    with pytest.raises(OSError, match="synthetic index failure"):
        eng.import_all(_backup(tmp_path, _row(eng, incoming_id, "Imported checklist")))
    assert _files(eng) == before


def test_orphan_body_collision_is_not_overwritten(tmp_path):
    eng = Engram(root=tmp_path / "store")
    eng._playbooks_dir.mkdir(parents=True, exist_ok=True)
    orphan = eng._playbooks_dir / "orphan.json"
    orphan.write_bytes(b"preserve unindexed body")
    eng.import_all(_backup(tmp_path, _row(eng, "orphan", "Imported checklist")))
    assert orphan.read_bytes() == b"preserve unindexed body"
    assert len(eng._read_playbook_index()) == 1
    assert eng._read_playbook_index()[0]["id"] != "orphan"


@pytest.mark.skipif(os.name != "nt", reason="Windows id comparison")
@pytest.mark.parametrize("pinned", [False, True])
def test_windows_import_case_collision_preserves_existing(tmp_path, pinned):
    eng = Engram(root=tmp_path / "store")
    eng._write_playbook_and_index(_row(eng, "abc", "Existing checklist"), create=True)
    if pinned:
        pinning.pin(eng, "abc")
    before = eng._playbook_path("abc").read_bytes()
    result = eng.import_all(_backup(tmp_path, _row(eng, "ABC", "Different checklist")))
    assert eng._playbook_path("abc").read_bytes() == before
    assert len(eng._read_playbook_index()) == (1 if pinned else 2)
    if pinned:
        assert "abc" in result["pinned"]["protected"]["playbooks"]


@pytest.mark.skipif(os.name != "nt", reason="Windows id comparison")
def test_windows_index_only_collision_is_reserved(tmp_path):
    eng = Engram(root=tmp_path / "store")
    eng._write_playbook_index([eng._playbook_index_entry(_row(eng, "abc", "Missing body"))])
    with pytest.raises(PlaybookIdExists):
        eng._write_playbook_and_index(_row(eng, "ABC", "New body"), create=True)
    assert not (eng._playbooks_dir / "ABC.json").exists()


def test_import_uses_exclusive_insertion_and_retries_a_collision(tmp_path, monkeypatch):
    eng = Engram(root=tmp_path / "store")
    real = eng._write_playbook_and_index
    calls = []

    def competing_insert(row, *, create=False):
        assert create
        calls.append(row["id"])
        if len(calls) == 1:
            real(_row(eng, row["id"], "Competing checklist"), create=True)
        return real(row, create=create)

    monkeypatch.setattr(eng, "_write_playbook_and_index", competing_insert)
    eng.import_all(_backup(tmp_path, _row(eng, "sample", "Imported checklist")))
    assert len(calls) == 2 and calls[0] != calls[1]
    assert eng._read_playbook_by_id("sample")["title"] == "Competing checklist"
    assert {r["title"] for r in eng._read_playbook_index()} == {"Competing checklist", "Imported checklist"}


@pytest.mark.parametrize("existing", [False, True])
def test_later_import_failure_rolls_back_only_imported_files(tmp_path, monkeypatch, existing):
    eng = Engram(root=tmp_path / "store")
    if existing:
        eng._write_playbook_and_index(_row(eng, "existing", "Existing checklist"), create=True)
    before = _files(eng)
    real = eng._update_playbook_index
    calls = 0

    def fail_second(mutator):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second insert failed")
        return real(mutator)

    monkeypatch.setattr(eng, "_update_playbook_index", fail_second)
    with pytest.raises(OSError, match="second insert failed"):
        eng.import_all(_backup(tmp_path, _row(eng, "first", "First import"), _row(eng, "second", "Second import")))
    assert _files(eng) == before
