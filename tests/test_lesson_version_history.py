from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore


def _raw_lessons(root: Path) -> list[dict]:
    return json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))


def test_update_lesson_snapshots_previous_content_and_links_version(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson(
        {"summary": "old lesson wording", "detail": "old detail", "domain": "testing", "tier": "verified"}
    )

    updated = eng.update_lesson(
        lesson["id"],
        {"summary": "new lesson wording", "detail": "new detail"},
    )

    assert updated["id"] == lesson["id"]
    assert updated["summary"] == "new lesson wording"

    raw = _raw_lessons(tmp_path)
    assert len(raw) == 2
    snapshot = next(item for item in raw if item["id"] != lesson["id"])
    assert snapshot["summary"] == "old lesson wording"
    assert snapshot["detail"] == "old detail"
    assert snapshot["status"] == "superseded"
    assert snapshot["tier"] == "archived"
    assert snapshot["snapshot_of"] == lesson["id"]
    assert snapshot["superseded_by"] == lesson["id"]

    edges = RelationStore(tmp_path).all_edges()
    assert {"src": lesson["id"], "rel": "supersedes", "dst": snapshot["id"]} in edges
    assert [item["summary"] for item in eng.get_lessons(limit=None, _update_access=False)] == [
        "new lesson wording"
    ]


def test_update_lesson_tier_only_does_not_create_version_snapshot(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson({"summary": "keep same content", "tier": "staging"})

    updated = eng.update_lesson(lesson["id"], {"tier": "verified"})

    assert updated["tier"] == "verified"
    assert len(_raw_lessons(tmp_path)) == 1
    assert RelationStore(tmp_path).all_edges() == []
