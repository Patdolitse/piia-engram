"""A tombstone record with a field of the wrong type is skipped and counted, never a crash.

Covers backup records (import preview and apply), local records in
knowledge/tombstones.jsonl (export, import union, the insert guard, doctor's
stale-version list and the pending check).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import tombstones
from piia_engram.core import Engram

_GOOD = {"id": "abc123def456", "kind": "lesson", "scope": "global", "h1": "a" * 64, "h2": "b" * 64,
         "hv": next(iter(sorted(tombstones.MATCHED_HASH_VERSIONS)))}

_BAD = [
    {**_GOOD, "id": "badkind00001", "kind": []},
    {**_GOOD, "id": "badkind00002", "kind": {"x": 1}},
    {**_GOOD, "id": "badscope0001", "scope": ["global"]},
    {**_GOOD, "id": "badhash00001", "h1": ["a"]},
    {**_GOOD, "id": "badhv0000001", "hv": [3]},
    {**_GOOD, "id": ["listid"]},
    {**_GOOD, "id": "badvia000001", "via": {"cli": 1}},
]


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=root)


def _write_local(eng: Engram, records: list) -> None:
    path = eng.root / "knowledge" / tombstones.FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _backup(tmp_path: Path, eng: Engram, stones: list) -> Path:
    out = Path(eng.export_all(str(tmp_path / "backup.json")))
    data = json.loads(out.read_text(encoding="utf-8"))
    data.setdefault("knowledge", data.get("knowledge") or {})
    target = data["knowledge"] if "knowledge" in data and isinstance(data["knowledge"], dict) else data
    target["tombstones"] = stones
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def test_malformed_backup_records_are_counted_not_raised(eng):
    from piia_engram.import_export import _check_tombstones

    clean, invalid = _check_tombstones([_GOOD, *_BAD])
    assert [r["id"] for r in clean] == [_GOOD["id"]]
    assert invalid == len(_BAD)


def test_malformed_local_records_do_not_break_export(eng, tmp_path):
    _write_local(eng, [_GOOD, *_BAD])
    summary = eng.export_all_with_summary(str(tmp_path / "out.json"))
    assert summary["skipped"]["tombstones"] == len(_BAD)
    data = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
    text = json.dumps(data)
    assert _GOOD["id"] in text and "badkind00001" not in text


def test_malformed_local_records_do_not_break_import_union(eng, tmp_path):
    _write_local(eng, [_GOOD, *_BAD])
    from piia_engram.import_export import _new_tombstones

    incoming = [{**_GOOD, "id": "fresh0000001", "h1": "c" * 64}]
    assert [r["id"] for r in _new_tombstones(tombstones.load(eng.root), incoming)] == ["fresh0000001"]
    result = eng._import_tombstones([*incoming, *_BAD], merge=True)
    assert "+1" in result and f"invalid {len(_BAD)}" in result


def test_malformed_local_records_do_not_break_lookups_and_inserts(eng):
    _write_local(eng, [_GOOD, *_BAD])
    row = {"id": "x", "summary": "a lesson that matches nothing", "domain": "t"}
    assert tombstones.lookup(eng.root, "lesson", row) is None
    assert tombstones.near(eng.root, "lesson", row) is None
    assert isinstance(tombstones.stale_version_ids(eng.root), list)
    assert eng.add_lesson("A lesson added next to malformed tombstones").get("id")
    assert isinstance(eng.tombstoned_but_pending(), list)
