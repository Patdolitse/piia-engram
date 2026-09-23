"""Version lineage stays reachable when rows live in the overflow archive (ADR-0002 §3.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram.storage import _append_jsonl_lines


@pytest.fixture()
def engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=tmp_path)


def _raw_archive(engram: Engram, kind: str, rows: list[dict]) -> None:
    _append_jsonl_lines(engram._overflow_archive_path(kind), [json.dumps(r) for r in rows])


def test_lineage_lookup_prefers_the_active_row(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "the active copy of this lesson"}, domain="x")["id"]
    _raw_archive(engram, "lesson", [{"id": lesson_id, "summary": "an older archived copy"}])
    kind, row, where = engram._find_lineage_record(lesson_id)
    assert (kind, where) == ("lesson", "active")
    assert row["summary"] == "the active copy of this lesson"


def test_lineage_lookup_falls_back_to_the_archive(engram: Engram):
    _raw_archive(engram, "decision", [{"id": "D-arch", "question": "which queue", "choice": "bounded"}])
    kind, row, where = engram._find_lineage_record("D-arch")
    assert (kind, where) == ("decision", "archive")
    assert row["choice"] == "bounded"


def test_an_id_archived_twice_resolves_to_the_highest_version_then_the_latest_stamp(engram: Engram):
    _raw_archive(engram, "lesson", [
        {"id": "L-dup", "summary": "v1 archived last", "version": 1, "overflow_archived_at": "2026-09-03T00:00:00Z"},
        {"id": "L-dup", "summary": "v2 archived first", "version": 2, "overflow_archived_at": "2026-09-01T00:00:00Z"},
        {"id": "L-dup", "summary": "v2 archived second", "version": 2, "overflow_archived_at": "2026-09-02T00:00:00Z"},
    ])
    assert engram._find_lineage_record("L-dup")[1]["summary"] == "v2 archived second"
    assert engram._archive_current_rows("lesson")["L-dup"]["summary"] == "v2 archived second"


def test_an_unknown_id_is_not_found(engram: Engram):
    assert engram._find_lineage_record("no-such-id") == (None, None, "")


def test_archive_ids_lists_every_archived_id(engram: Engram):
    _raw_archive(engram, "lesson", [{"id": "L-a", "summary": "a"}, {"id": "L-b", "summary": "b"}])
    assert engram._archive_ids("lesson") == {"L-a", "L-b"}
    assert engram._archive_ids("decision") == set()


# -- snapshots go to the archive --------------------------------------------------------


def _active_ids(engram: Engram, kind: str) -> list[str]:
    path = engram._knowledge_dir / f"{kind}s.json"
    return [r["id"] for r in engram._read_entries(path, kind, migrate=False)]


def _archived(engram: Engram, kind: str) -> list[tuple[str, str]]:
    return [(r["id"], r.get("overflow_archive_reason")) for r in engram._read_overflow_archive(kind)]


def test_a_lesson_edit_puts_its_snapshot_in_the_archive(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "first text of the lesson"}, domain="x")["id"]
    result = engram.update_knowledge(lesson_id, {"summary": "second text of the lesson"})
    assert "overflow_archived_ids" not in result
    assert _active_ids(engram, "lesson") == [lesson_id]
    assert _archived(engram, "lesson") == [(f"{lesson_id}-prev-v1", "snapshot")]
    snapshot = engram._read_overflow_archive("lesson")[0]
    assert snapshot["summary"] == "first text of the lesson"
    assert snapshot["snapshot_of"] == lesson_id


def test_a_decision_edit_puts_its_snapshot_in_the_archive(engram: Engram):
    decision_id = engram.add_decision(
        {"question": "Which queue shape?", "choice": "A bounded queue", "reasoning": "Predictable size."}
    )["id"]
    engram.update_knowledge(decision_id, {"choice": "An unbounded queue"})
    assert _active_ids(engram, "decision") == [decision_id]
    assert _archived(engram, "decision") == [(f"{decision_id}-prev-v1", "snapshot")]


def test_snapshots_are_named_by_version_and_history_reads_them_from_the_archive(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "version one of the lesson"}, domain="x")["id"]
    engram.update_knowledge(lesson_id, {"summary": "version two of the lesson"})
    engram.update_knowledge(lesson_id, {"summary": "version three of the lesson"})
    assert [i for i, _ in _archived(engram, "lesson")] == [f"{lesson_id}-prev-v1", f"{lesson_id}-prev-v2"]
    history = engram.get_knowledge_history(lesson_id, include_bodies=True)
    assert [(n["snapshot_version"], n["summary"]) for n in history["snapshots"]] == [
        (2, "version two of the lesson"), (1, "version one of the lesson")
    ]
    assert engram.get_knowledge_history(lesson_id, version=1)["snapshot"]["id"] == f"{lesson_id}-prev-v1"


def test_a_snapshot_id_already_in_the_archive_gets_a_suffix(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "text before the edit"}, domain="x")["id"]
    _raw_archive(engram, "lesson", [{"id": f"{lesson_id}-prev-v1", "summary": "an unrelated archived row"}])
    engram.update_knowledge(lesson_id, {"summary": "text after the edit"})
    assert (f"{lesson_id}-prev-v1-2", "snapshot") in _archived(engram, "lesson")


def test_history_of_an_archived_head_is_still_readable(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "head text version one"}, domain="x", tier="staging")["id"]
    engram.update_knowledge(lesson_id, {"summary": "head text version two"})
    path = engram._knowledge_dir / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [r for r in rows if r["id"] != lesson_id])
    history = engram.get_knowledge_history(lesson_id)
    assert history["head_version"] == 2
    assert [n["id"] for n in history["snapshots"]] == [f"{lesson_id}-prev-v1"]


# -- updates that target an archived id -------------------------------------------------


def test_updating_an_archived_snapshot_is_refused_as_immutable(engram: Engram):
    lesson_id = engram.add_lesson({"summary": "text before the edit"}, domain="x")["id"]
    engram.update_knowledge(lesson_id, {"summary": "text after the edit"})
    result = engram.update_knowledge(f"{lesson_id}-prev-v1", {"detail": "tampered"})
    assert result["error"] == "snapshot_immutable"


@pytest.mark.parametrize("via", ["update_knowledge", "update_lesson"])
def test_updating_an_archived_row_points_to_restore(engram: Engram, via: str):
    lesson_id = engram.add_lesson({"summary": "a lesson that gets archived"}, domain="x", tier="staging")["id"]
    path = engram._knowledge_dir / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [r for r in rows if r["id"] != lesson_id])
    result = getattr(engram, via)(lesson_id, {"detail": "edited"})
    assert result["error"] == "archived"
    assert result["item_id"] == lesson_id
    assert result["hint"] == "retention restore"
