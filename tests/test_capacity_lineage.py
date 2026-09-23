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
