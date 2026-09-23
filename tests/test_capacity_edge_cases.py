"""Capacity edge cases: the row being edited, reconcile audit, import re-runs, batch replies."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.storage import _append_jsonl_lines


@pytest.fixture()
def engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    return Engram(root=tmp_path / "store")


def _old_queue(engram: Engram, n: int) -> list[str]:
    from knowledge_seed import raw_write_json

    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"id": f"L{i:03d}", "summary": f"queued lesson {i}", "tier": "staging", "status": "active",
             "created_at": old} for i in range(n)]
    raw_write_json(engram._knowledge_dir / "lessons.json", rows)
    return [r["id"] for r in rows]


def _active_ids(engram: Engram, kind: str = "lesson") -> list[str]:
    path = engram._knowledge_dir / f"{kind}s.json"
    return [r["id"] for r in engram._read_entries(path, kind, migrate=False)]


def test_editing_a_queued_row_never_moves_that_row(engram: Engram, monkeypatch):
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "2")
    ids = _old_queue(engram, 4)
    result = engram.update_knowledge(ids[0], {"summary": "an edited queued lesson"})
    assert result["id"] == ids[0]
    assert ids[0] in _active_ids(engram)
    assert sorted(result["overflow_archived_ids"]) == sorted(ids[1:3])


def test_merging_keeps_the_primary_in_the_active_file(engram: Engram, monkeypatch):
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "2")
    ids = _old_queue(engram, 4)
    result = engram.merge_knowledge(ids[0], ids[1])
    assert "error" not in result
    assert ids[0] in _active_ids(engram)


def test_reconcile_apply_keeps_the_capacity_audit_events(engram: Engram, monkeypatch):
    from piia_engram.reconcile_apply import apply_reconcile

    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "1")
    monkeypatch.setenv("ENGRAM_REVIEW_MIN_STAY_DAYS", "0")
    apply_reconcile(engram, [{"summary": "first reconciled lesson text"},
                             {"summary": "second reconciled lesson text"}], confirm=True, dry_run=False)
    lines = (engram.root / "audit.log").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line]
    assert [e["detail"].split(" ")[0] for e in events if e.get("action") == "archive"] == ["review_queue_quota"]
    assert not any("first reconciled lesson text" in json.dumps(e, ensure_ascii=False) for e in events)


def test_rerunning_an_interrupted_materializing_import_restores_the_edge(engram: Engram, tmp_path, monkeypatch):
    local = engram.add_lesson({"summary": "materialize topic for reruns", "detail": "local body",
                               "domain": "imp", "tier": "verified"})
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"schema_version": "2.0", "knowledge": {"lessons": [
        {"id": "L-in", "summary": "materialize topic for reruns", "detail": "incoming body",
         "domain": "imp", "tier": "verified"}]}}), encoding="utf-8")
    real = Engram._import_relations_locked

    def _crash(self, *args, **kwargs):
        raise OSError("crash before relations")

    monkeypatch.setattr(Engram, "_import_relations_locked", _crash)
    with pytest.raises(OSError):
        engram.import_all(str(backup), merge=True, materialize_version_chain=True)
    monkeypatch.setattr(Engram, "_import_relations_locked", real)
    engram.import_all(str(backup), merge=True, materialize_version_chain=True)
    edges = {(e["src"], e["dst"]) for e in RelationStore(engram.root).all_edges() if e["rel"] == "supersedes"}
    assert edges == {("L-in", local["id"])}
    assert not (engram._knowledge_dir / ".import-pending.json").exists()


def test_tier_evaluation_marks_rows_inside_the_lock_only(engram: Engram, monkeypatch):
    staged = engram.add_lesson({"summary": "a staged lesson read often", "domain": "x", "tier": "staging"})
    kept = engram.add_lesson({"summary": "a reviewed lesson", "domain": "x", "tier": "verified"})
    path = engram._knowledge_dir / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [dict(r, access_count=9) for r in rows])

    def _no_whole_file_write(*args, **kwargs):
        raise AssertionError("evaluate_tiers must not replace the whole file")

    monkeypatch.setattr(Engram, "_write_entries", _no_whole_file_write)
    assert engram.evaluate_tiers()["suggested"] == 1
    rows = {r["id"]: r for r in engram._read_entries(path, "lesson", migrate=False)}
    assert rows[staged["id"]]["promotion_suggested"] is True
    assert kept["id"] in rows
    assert engram._read_overflow_archive("lesson") == []

def test_the_mcp_boundary_drops_system_only_status_values():
    from piia_engram.storage import strip_untrusted_trust_fields

    assert "status" not in strip_untrusted_trust_fields({"summary": "x", "status": "superseded"})
    assert strip_untrusted_trust_fields({"summary": "x", "status": "outdated"})["status"] == "outdated"


def test_a_batch_reports_rows_placed_in_the_archive(engram: Engram, monkeypatch):
    from piia_engram import mcp_server  # noqa: F401  (loads the tool modules in order)
    from piia_engram.mcp_tools_write import _overflow_note

    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "1", "ENGRAM_REVIEW_QUEUE_CEILING": "1",
                        "ENGRAM_REVIEW_MIN_STAY_DAYS": "7"}.items():
        monkeypatch.setenv(name, value)
    result = engram.bulk_add_lessons([
        {"summary": "first queued batch lesson", "domain": "x", "tier": "staging"},
        {"summary": "second queued batch lesson", "domain": "x", "tier": "staging"},
    ])
    statuses = [item["status"] for item in result["results"]]
    assert statuses == ["saved", "archived"]
    note = _overflow_note(result)
    assert "1 条新条目已直接放入溢出归档" in note
    assert "较早的条目" not in note


def test_a_new_row_never_takes_the_id_of_an_archived_row(engram: Engram):
    _append_jsonl_lines(engram._overflow_archive_path("lesson"), [json.dumps(
        {"id": "L-taken", "summary": "archived and restorable", "tier": "archived",
         "archived_from_tier": "verified", "status": "active"})])
    added = engram.add_lesson({"id": "L-taken", "summary": "a different new lesson", "domain": "x"})
    assert added["id"] != "L-taken"
    assert engram.restore_lifecycle_archive("L-taken")["changed"] is True
