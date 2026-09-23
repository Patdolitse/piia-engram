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


# -- pending supersede: unreviewed rows never hide reviewed rows (I10) -------------------


def _supersedes_edges(engram: Engram) -> set[tuple[str, str]]:
    from piia_engram.governance_store import RelationStore

    return {(e["src"], e["dst"]) for e in RelationStore(engram.root).all_edges() if e["rel"] == "supersedes"}


def _decision(engram: Engram, choice: str, **extra) -> dict:
    return engram.add_decision(
        {"question": "Which storage format should the queue use?", "choice": choice,
         "reasoning": "Recorded for the lineage tests.", **extra}
    )


def _active_row(engram: Engram, kind: str, item_id: str) -> dict:
    path = engram._knowledge_dir / f"{kind}s.json"
    return next(r for r in engram._read_entries(path, kind, migrate=False) if r["id"] == item_id)


def test_an_unreviewed_decision_records_a_pending_supersede_instead_of_an_edge(engram: Engram):
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    new = _decision(engram, "SQLite", tier="staging")["id"]
    assert _supersedes_edges(engram) == set()
    assert _active_row(engram, "decision", new)["pending_supersedes"] == old


def test_an_explicit_supersede_from_an_unreviewed_decision_is_pending_too(engram: Engram):
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    other = engram.add_decision({"question": "Where should backups go?", "choice": "Local disk",
                                 "reasoning": "Explicit supersede test.", "supersedes": old, "tier": "staging"})["id"]
    assert _supersedes_edges(engram) == set()
    assert _active_row(engram, "decision", other)["pending_supersedes"] == old


def test_promotion_writes_the_pending_edge_and_clears_the_field(engram: Engram):
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    new = _decision(engram, "SQLite", tier="staging")["id"]
    assert engram.promote_knowledge(new)["status"] == "promoted"
    assert _supersedes_edges(engram) == {(new, old)}
    assert "pending_supersedes" not in _active_row(engram, "decision", new)


def test_a_rejected_decision_never_writes_its_pending_edge(engram: Engram):
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    new = _decision(engram, "SQLite", tier="staging")["id"]
    assert "error" not in engram.update_knowledge(new, {"status": "rejected"})
    assert _supersedes_edges(engram) == set()


def test_a_reviewed_decision_writes_its_edge_at_once(engram: Engram):
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    new = _decision(engram, "SQLite", tier="verified")["id"]
    assert _supersedes_edges(engram) == {(new, old)}
    assert "pending_supersedes" not in _active_row(engram, "decision", new)


def test_a_supersede_target_in_the_archive_still_counts_as_present(engram: Engram):
    old = _decision(engram, "JSON lines", tier="staging")["id"]
    path = engram._knowledge_dir / "decisions.json"
    engram._update_entries(path, "decision", lambda rows: [r for r in rows if r["id"] != old])
    new = engram.add_decision({"question": "A different question entirely?", "choice": "Yes",
                               "reasoning": "Archived target test.", "supersedes": old, "tier": "verified"})["id"]
    assert _supersedes_edges(engram) == {(new, old)}


def test_a_decision_placed_in_the_archive_writes_no_edge(engram: Engram, monkeypatch):
    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "1", "ENGRAM_REVIEW_QUEUE_CEILING": "1",
                        "ENGRAM_REVIEW_MIN_STAY_DAYS": "7"}.items():
        monkeypatch.setenv(name, value)
    old = _decision(engram, "JSON lines", tier="verified")["id"]
    engram.add_decision({"question": "Unrelated queued decision?", "choice": "Maybe",
                         "reasoning": "Fills the queue.", "tier": "staging"})
    placed = _decision(engram, "SQLite", tier="staging")
    assert placed["placement"] == "archived"
    assert _supersedes_edges(engram) == set()
    archived = engram._archive_current_rows("decision")[placed["id"]]
    assert archived["pending_supersedes"] == old


# -- honored edges: the read side of I10 -------------------------------------------------


def _legacy_edge(engram: Engram, src: str, dst: str) -> None:
    from piia_engram.governance_store import RelationStore

    RelationStore(engram.root).add_relation(src, "supersedes", dst)


def test_honored_edges_drop_only_unreviewed_rows_superseding_reviewed_rows():
    from piia_engram import version_chain

    edges = [
        {"src": "q", "rel": "supersedes", "dst": "v"},
        {"src": "v2", "rel": "supersedes", "dst": "v"},
        {"src": "q", "rel": "supersedes", "dst": "q0"},
        {"src": "q", "rel": "led_to", "dst": "v"},
    ]
    assert version_chain.honored_edges(edges, {"v", "v2"}) == edges[1:]


def test_a_legacy_unreviewed_edge_does_not_hide_a_reviewed_lesson_from_recall(engram: Engram):
    from piia_engram import recall_service

    reviewed = engram.add_lesson({"summary": "importer uses bounded queues"}, domain="x", tier="verified")["id"]
    unreviewed = engram.add_lesson({"summary": "importer uses unbounded queues"}, domain="x", tier="staging")["id"]
    _legacy_edge(engram, unreviewed, reviewed)
    sources = recall_service.gather_recall_sources(engram, query="importer bounded queues")
    assert sources["collapsed_count"] == 0
    ids = {item.get("id") for item in sources["relevant"] + sources["query_knowledge"]}
    assert reviewed in ids


def test_a_legacy_unreviewed_edge_does_not_hide_a_reviewed_decision_from_the_resume_brief(engram: Engram):
    reviewed = _decision(engram, "JSON lines", tier="verified")["id"]
    unreviewed = engram.add_decision({"question": "Unrelated queued decision?", "choice": "Maybe",
                                      "reasoning": "Legacy edge source.", "tier": "staging"})["id"]
    _legacy_edge(engram, unreviewed, reviewed)
    assert "JSON lines" in engram.get_resume_brief()["markdown"]


# -- decision history and thread over active plus archive (I3) --------------------------

QUESTION = "Which storage format should the queue use?"


def _drop_from_active(engram: Engram, kind: str, item_id: str) -> None:
    path = engram._knowledge_dir / f"{kind}s.json"
    engram._update_entries(path, kind, lambda rows: [r for r in rows if r["id"] != item_id])


def test_decision_history_keeps_the_reviewed_decision_current(engram: Engram):
    reviewed = _decision(engram, "JSON lines", tier="verified")["id"]
    unreviewed = _decision(engram, "SQLite", tier="staging")["id"]
    _legacy_edge(engram, unreviewed, reviewed)
    history = engram.get_decision_history(QUESTION)
    assert history["current"]["id"] == reviewed
    assert {r["id"] for r in history["revisions"]} == {reviewed, unreviewed}


def test_an_archived_decision_stays_in_its_history_and_thread(engram: Engram):
    older = _decision(engram, "JSON lines", tier="staging")["id"]
    newer = _decision(engram, "SQLite", tier="verified")["id"]
    assert _supersedes_edges(engram) == {(newer, older)}
    _drop_from_active(engram, "decision", older)
    history = engram.get_decision_history(QUESTION)
    by_id = {r["id"]: r for r in history["revisions"]}
    assert by_id[older]["status"] == "superseded"
    assert by_id[older]["archived"] is True
    assert history["current"]["id"] == newer
    thread = engram.get_decision_thread(newer)
    summaries = {row["id"]: row.get("summary") for row in thread["order"]}
    assert summaries[older]


def test_decision_history_falls_back_to_the_archive_when_nothing_is_active(engram: Engram):
    only = _decision(engram, "JSON lines", tier="staging")["id"]
    _drop_from_active(engram, "decision", only)
    history = engram.get_decision_history(QUESTION)
    assert history["current"]["id"] == only
