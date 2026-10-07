"""JSON backups carry rejection tombstones (hashes only), so a restored store
still refuses what the Owner rejected before."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import tombstones
from piia_engram.cli_commands import _render_import_result_text
from piia_engram.core import Engram

CLAIMS = {
    "lesson": {"summary": "Never ship a release on a Friday evening"},
    "decision": {"title": "Release cadence", "question": "Ship releases on Friday evenings?",
                 "choice": "No, ship early in the week"},
    "playbook": {"title": "Rotate the release signing key",
                 "steps": [{"action": "Revoke the previous key"}, "Publish the replacement key"]},
}
CLAIM_STRINGS = (
    "Never ship a release on a Friday evening",
    "Ship releases on Friday evenings?",
    "No, ship early in the week",
    "Rotate the release signing key",
    "Revoke the previous key",
    "Publish the replacement key",
)


@pytest.fixture(autouse=True)
def _no_approval_mode(monkeypatch):
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)


def _store(tmp_path: Path, name: str) -> Engram:
    return Engram(root=tmp_path / name)


def _write_stone(root: Path, kind: str, row: dict, *, version: int, item_id: str, **extra) -> None:
    if version == 2:
        h1, h2 = tombstones._hashes_v2(tombstones.claim_text(kind, row))
    else:
        h1, h2 = tombstones.claim_hashes(kind, row)
    path = Path(root) / "knowledge" / tombstones.FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"id": item_id, "kind": kind, "scope": "global", "h1": h1, "h2": h2, "hv": version,
              "rejected_at": "2026-01-01T00:00:00Z", "via": "test", **extra}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _propose(eng: Engram, kind: str) -> dict:
    row = dict(CLAIMS[kind])
    if kind == "lesson":
        return eng.add_lesson(dict(row, domain="t"))
    if kind == "decision":
        return eng.add_decision(row)
    return eng.add_playbook(row)


def _backup(tmp_path: Path, payload: dict, name: str = "backup.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "replace"])
@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_restored_store_still_refuses_rejected_content(tmp_path, kind, version, merge):
    src = _store(tmp_path, "src")
    _write_stone(src.root, kind, CLAIMS[kind], version=version, item_id=f"rej-{kind}-{version}")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    assert _propose(_store(tmp_path, "control"), kind).get("status") != "rejected_before"
    result = dst.import_all(out, merge=merge)
    assert result["status"] == "success"

    again = _propose(dst, kind)
    assert again["status"] == "rejected_before"
    assert again["rejection_id"] == f"rej-{kind}-{version}"
    restored = tombstones.load(dst.root)
    assert [r["hv"] for r in restored] == [version]


def test_export_carries_hashes_and_metadata_only(tmp_path):
    src = _store(tmp_path, "src")
    for i, kind in enumerate(CLAIMS):
        # An unexpected text field on disk must not travel with the backup.
        _write_stone(src.root, kind, CLAIMS[kind], version=3, item_id=f"r{i}",
                     summary=CLAIMS[kind].get("summary", "stray text"), prior_rejection_id="older")
    out = src.export_all(str(tmp_path / "backup.json"))

    text = Path(out).read_text(encoding="utf-8")
    for claim in CLAIM_STRINGS:
        assert claim not in text
    assert "stray text" not in text
    stones = json.loads(text)["knowledge"]["tombstones"]
    assert len(stones) == 3
    allowed = {"id", "kind", "scope", "h1", "h2", "hv", "rejected_at", "via", "prior_rejection_id"}
    for stone in stones:
        assert set(stone) <= allowed
        assert stone["hv"] == 3 and stone["prior_rejection_id"] == "older"


def test_import_drops_text_fields_and_malformed_records(tmp_path):
    good = {"id": "ok1", "kind": "lesson", "scope": "global", "h1": "a" * 64, "h2": "b" * 64,
            "hv": 3, "summary": "claim text that must not land"}
    payload = {"schema_version": "2.0", "knowledge": {"tombstones": [
        good, "not a record", {"kind": "lesson", "h1": "c" * 64}, {"id": "no-hash"},
    ]}}
    dst = _store(tmp_path, "dst")
    dst.import_all(_backup(tmp_path, payload), merge=True)

    restored = tombstones.load(dst.root)
    assert [r["id"] for r in restored] == ["ok1"]
    assert "summary" not in restored[0]
    raw = (dst.root / "knowledge" / tombstones.FILENAME).read_text(encoding="utf-8")
    assert "claim text" not in raw


def test_merge_dedupes_by_id_and_by_hash(tmp_path):
    src = _store(tmp_path, "src")
    _write_stone(src.root, "lesson", CLAIMS["lesson"], version=3, item_id="same-id")
    _write_stone(src.root, "playbook", CLAIMS["playbook"], version=2, item_id="pb-src")
    _write_stone(src.root, "decision", CLAIMS["decision"], version=3, item_id="dec-src")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", {"summary": "something else entirely"}, version=3, item_id="same-id")
    _write_stone(dst.root, "playbook", CLAIMS["playbook"], version=2, item_id="pb-local")
    preview = dst.import_all(out, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"] == {
        "incoming": 3, "would_add": 1, "would_skip": 2, "conflicts": 0}

    first = dst.import_all(out, merge=True)
    assert "tombstones(+1)" in first["imported"]
    second = dst.import_all(out, merge=True)
    assert "tombstones(+0)" in second["imported"]
    assert sorted(r["id"] for r in tombstones.load(dst.root)) == ["dec-src", "pb-local", "same-id"]


def test_replace_mode_replaces_the_tombstone_set(tmp_path):
    src = _store(tmp_path, "src")
    _write_stone(src.root, "decision", CLAIMS["decision"], version=2, item_id="from-backup")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="local-only")
    preview = dst.import_all(out, merge=False, dry_run=True)
    assert preview["summary"]["tombstones"]["would_add"] == 1
    result = dst.import_all(out, merge=False)

    assert "tombstones(1)" in result["imported"]
    assert [r["id"] for r in tombstones.load(dst.root)] == ["from-backup"]
    assert _propose(dst, "decision")["status"] == "rejected_before"


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "replace"])
def test_old_export_without_tombstones_still_imports(tmp_path, merge):
    payload = {"schema_version": "2.0", "exported_at": "2026-01-01T00:00:00Z",
               "knowledge": {"lessons": [{"summary": "An older backup row", "domain": "t"}]}}
    path = _backup(tmp_path, payload)
    dst = _store(tmp_path, "dst")
    _write_stone(dst.root, "lesson", CLAIMS["lesson"], version=3, item_id="kept")

    preview = dst.import_all(path, merge=merge, dry_run=True)
    assert "tombstones" not in preview["summary"]
    result = dst.import_all(path, merge=merge)

    assert result["status"] == "success"
    assert not any(str(item).startswith("tombstones") for item in result["imported"])
    # A backup without the section leaves the store's tombstones alone in both modes.
    assert [r["id"] for r in tombstones.load(dst.root)] == ["kept"]
    assert any(r.get("summary") == "An older backup row"
               for r in dst.get_lessons(limit=None, _update_access=False))


def test_dry_run_shows_the_tombstone_count_and_writes_nothing(tmp_path):
    src = _store(tmp_path, "src")
    for kind in CLAIMS:
        _write_stone(src.root, kind, CLAIMS[kind], version=3, item_id=f"r-{kind}")
    out = src.export_all(str(tmp_path / "backup.json"))

    dst = _store(tmp_path, "dst")
    preview = dst.import_all(out, merge=True, dry_run=True)
    assert preview["summary"]["tombstones"] == {
        "incoming": 3, "would_add": 3, "would_skip": 0, "conflicts": 0}
    assert tombstones.load(dst.root) == []
    rendered = _render_import_result_text(preview)
    assert "tombstones: incoming=3 add=3 skip=0" in rendered
