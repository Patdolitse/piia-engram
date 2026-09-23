"""Imports run under the capacity rules in one locked section (v4.21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.storage import _append_jsonl_lines, _read_jsonl_rows

KINDS = ("lesson", "decision")


@pytest.fixture()
def engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    return Engram(root=tmp_path / "store")


def _row(kind: str, i: int, salt: str, **extra) -> dict:
    if kind == "lesson":
        return {"summary": f"{salt} lesson number {i} about imports", "domain": "imp", **extra}
    return {"question": f"{salt} question number {i} about imports?", "choice": f"choice {i}",
            "reasoning": "import test", **extra}


def _backup(tmp_path: Path, name: str, knowledge: dict, archive: dict | None = None) -> str:
    payload = {"schema_version": "2.0", "knowledge": knowledge}
    if archive is not None:
        payload["overflow_archive"] = archive
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _active(engram: Engram, kind: str) -> list[dict]:
    return engram._read_entries(engram._knowledge_dir / f"{kind}s.json", kind, migrate=False)


def _archive_lines(engram: Engram, kind: str) -> list[dict]:
    return _read_jsonl_rows(engram._overflow_archive_path(kind))[0]


@pytest.mark.parametrize("kind", KINDS)
def test_merge_keeps_every_reviewed_row_instead_of_cutting_by_position(engram: Engram, tmp_path, kind):
    for i in range(3):
        add = engram.add_lesson if kind == "lesson" else engram.add_decision
        add(_row(kind, i, "LOCAL", tier="verified"))
    incoming = [dict(_row(kind, i, "IMP", tier="verified"), id=f"imp-{i}") for i in range(5)]
    result = engram.import_all(_backup(tmp_path, "b", {f"{kind}s": incoming}), merge=True)
    assert f"{kind}s(+5)" in result["imported"]
    assert len(_active(engram, kind)) == 8
    assert _archive_lines(engram, kind) == []


def test_merge_skips_rows_already_in_the_archive(engram: Engram, tmp_path):
    archived = dict(_row("lesson", 1, "OLD"), id="L-old")
    _append_jsonl_lines(engram._overflow_archive_path("lesson"), [json.dumps(archived)])
    incoming = [dict(_row("lesson", 1, "OLD"), id="L-other-id"), dict(_row("lesson", 2, "NEW"), id="L-new")]
    result = engram.import_all(_backup(tmp_path, "b", {"lessons": incoming}), merge=True)
    assert "lessons(+1)" in result["imported"]
    assert [r["id"] for r in _active(engram, "lesson")] == ["L-new"]


def test_merge_places_queue_rows_beyond_the_ceiling_in_the_archive(engram: Engram, tmp_path, monkeypatch):
    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "2", "ENGRAM_REVIEW_QUEUE_CEILING": "3",
                        "ENGRAM_REVIEW_MIN_STAY_DAYS": "7"}.items():
        monkeypatch.setenv(name, value)
    incoming = [dict(_row("lesson", i, "Q", tier="staging"), id=f"q-{i}") for i in range(5)]
    result = engram.import_all(_backup(tmp_path, "b", {"lessons": incoming}), merge=True)
    assert "lessons(+5, archived 2)" in result["imported"]
    assert len(_active(engram, "lesson")) == 3
    assert {r["overflow_archive_reason"] for r in _archive_lines(engram, "lesson")} == {"review_queue_full"}


def test_replace_archives_local_rows_the_file_drops_or_changes(engram: Engram, tmp_path):
    kept = engram.add_lesson(_row("lesson", 1, "KEEP", tier="verified"))
    changed = engram.add_lesson(_row("lesson", 2, "CHANGE", tier="verified"))
    dropped = engram.add_lesson(_row("lesson", 3, "DROP", tier="verified"))
    incoming = [
        {k: kept[k] for k in ("id", "summary", "domain", "tier")},
        dict({k: changed[k] for k in ("id", "domain", "tier")}, summary="a new body for the changed row"),
        dict(_row("lesson", 4, "NEWROW", tier="verified"), id="L-brand-new"),
    ]
    engram.import_all(_backup(tmp_path, "b", {"lessons": incoming}), merge=False)
    assert sorted(r["id"] for r in _active(engram, "lesson")) == sorted([kept["id"], changed["id"], "L-brand-new"])
    archived = {(r["id"], r["summary"], r["overflow_archive_reason"]) for r in _archive_lines(engram, "lesson")}
    assert archived == {
        (changed["id"], changed["summary"], "import_replace"),
        (dropped["id"], dropped["summary"], "import_replace"),
    }


def test_replace_keeps_local_relations_next_to_the_file_edges(engram: Engram, tmp_path):
    RelationStore(engram.root).add_relation("local-a", "led_to", "local-b")
    engram.import_all(_backup(tmp_path, "b", {
        "lessons": [dict(_row("lesson", 1, "R"), id="L-r")],
        "relations": [{"src": "file-a", "rel": "led_to", "dst": "file-b"}],
    }), merge=False)
    edges = {(e["src"], e["dst"]) for e in RelationStore(engram.root).all_edges()}
    assert edges == {("local-a", "local-b"), ("file-a", "file-b")}


def test_the_archive_segment_is_written_back_once(engram: Engram, tmp_path):
    segment = {"lessons": [dict(_row("lesson", 1, "ARCH"), id="L-arch", overflow_archive_reason="review_queue_quota",
                                overflow_archived_at="2026-09-01T00:00:00Z")], "decisions": []}
    backup = _backup(tmp_path, "b", {"lessons": [dict(_row("lesson", 2, "ACT"), id="L-act")]}, archive=segment)
    engram.import_all(backup, merge=True)
    engram.import_all(backup, merge=True)
    lines = _archive_lines(engram, "lesson")
    assert [(r["id"], r["overflow_archive_reason"]) for r in lines] == [("L-arch", "review_queue_quota")]
    assert lines[0]["overflow_archived_at"] == "2026-09-01T00:00:00Z"


def test_rows_without_ids_are_not_duplicated_by_a_second_replace(engram: Engram, tmp_path):
    backup = _backup(tmp_path, "b", {"lessons": [_row("lesson", i, "NOID") for i in range(3)]})
    engram.import_all(backup, merge=False)
    first = sorted(r["id"] for r in _active(engram, "lesson"))
    engram.import_all(backup, merge=False)
    assert sorted(r["id"] for r in _active(engram, "lesson")) == first
    assert _archive_lines(engram, "lesson") == []


def test_an_interrupted_import_leaves_a_marker_and_a_rerun_completes(engram: Engram, tmp_path, monkeypatch):
    backup = _backup(tmp_path, "b", {
        "lessons": [dict(_row("lesson", 1, "INT"), id="L-int")],
        "decisions": [dict(_row("decision", 1, "INT"), id="D-int")],
    })
    real = Engram._update_entries

    def _fail_on_decisions(self, path, entry_type, *args, **kwargs):
        if entry_type == "decision":
            raise OSError("disk went away")
        return real(self, path, entry_type, *args, **kwargs)

    monkeypatch.setattr(Engram, "_update_entries", _fail_on_decisions)
    with pytest.raises(OSError):
        engram.import_all(backup, merge=True)
    marker = engram._knowledge_dir / ".import-pending.json"
    assert marker.is_file()
    monkeypatch.setattr(Engram, "_update_entries", real)
    engram.import_all(backup, merge=True)
    assert not marker.exists()
    assert [r["id"] for r in _active(engram, "lesson")] == ["L-int"]
    assert [r["id"] for r in _active(engram, "decision")] == ["D-int"]


def test_an_unreviewed_version_candidate_leaves_the_reviewed_row_active(engram: Engram, tmp_path):
    local = engram.add_lesson({"summary": "materialize topic for imports", "detail": "local body",
                               "domain": "imp", "tier": "verified"})
    incoming = {"id": "L-incoming", "summary": "materialize topic for imports", "detail": "incoming body",
                "domain": "imp", "tier": "staging"}
    result = engram.import_all(_backup(tmp_path, "b", {"lessons": [incoming]}), merge=True,
                               materialize_version_chain=True)
    item = result["version_chain_materialization"]["items"][0]
    assert item["outcome"] == "materialized" and item["pending_review"] is True
    rows = {r["id"]: r for r in _active(engram, "lesson")}
    assert rows[local["id"]]["status"] == "active"
    assert rows[item["new_id"]]["pending_supersedes"] == local["id"]
    assert RelationStore(engram.root).all_edges() == []


def test_import_moves_are_audited_with_id_and_reason_only(engram: Engram, tmp_path, monkeypatch):
    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "1", "ENGRAM_REVIEW_QUEUE_CEILING": "1",
                        "ENGRAM_REVIEW_MIN_STAY_DAYS": "7"}.items():
        monkeypatch.setenv(name, value)
    incoming = [dict(_row("lesson", i, "AUD", tier="staging"), id=f"aud-{i}") for i in range(2)]
    engram.import_all(_backup(tmp_path, "b", {"lessons": incoming}), merge=True)
    lines = (engram.root / "audit.log").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line]
    archive = [(e["resource"], e["detail"], e.get("source_tool")) for e in events if e.get("action") == "archive"]
    assert archive == [("knowledge/lessons", "review_queue_full id=aud-0", "import")]
