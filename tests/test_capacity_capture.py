"""Captures compare against the overflow archive, not only the active files (v4.21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram.storage import _append_jsonl_lines


@pytest.fixture()
def engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=tmp_path / "store")


def _archive(engram: Engram, kind: str, rows: list[dict]) -> None:
    _append_jsonl_lines(engram._overflow_archive_path(kind), [json.dumps(r) for r in rows])


def _active_ids(engram: Engram, kind: str) -> list[str]:
    path = engram._knowledge_dir / f"{kind}s.json"
    return [r["id"] for r in engram._read_entries(path, kind, migrate=False)]


def test_a_queued_lesson_already_in_the_archive_is_a_duplicate(engram: Engram):
    _archive(engram, "lesson", [{"id": "L-arch", "summary": "archived capture about queues", "domain": "x"}])
    result = engram.add_lesson({"summary": "archived capture about queues", "domain": "x", "tier": "staging"})
    assert result["status"] == "duplicate"
    assert result["in_overflow_archive"] is True
    assert result["existing_id"] == "L-arch"
    assert _active_ids(engram, "lesson") == []


def test_a_queued_decision_already_in_the_archive_is_a_duplicate(engram: Engram):
    _archive(engram, "decision", [{"id": "D-arch", "question": "Which queue shape?", "choice": "Bounded"}])
    result = engram.add_decision({"question": "Which queue shape?", "choice": "Bounded",
                                  "reasoning": "Same as the archived one.", "tier": "staging"})
    assert result["status"] == "duplicate" and result["in_overflow_archive"] is True
    other = engram.add_decision({"question": "Which queue shape?", "choice": "Unbounded",
                                 "reasoning": "A different choice is not a duplicate.", "tier": "staging"})
    assert other.get("status") != "duplicate"


def test_a_reviewed_write_is_not_blocked_by_an_archived_copy(engram: Engram):
    _archive(engram, "lesson", [{"id": "L-arch", "summary": "reviewed text also in the archive", "domain": "x"}])
    result = engram.add_lesson({"summary": "reviewed text also in the archive", "domain": "x", "tier": "verified"})
    assert result.get("status") != "duplicate"
    assert _active_ids(engram, "lesson") == [result["id"]]


def test_the_memory_md_import_skips_lessons_in_the_archive(engram: Engram, tmp_path: Path):
    from piia_engram.compat import import_from_openclaw

    _archive(engram, "lesson", [{"id": "L-arch", "summary": "a lesson that was archived", "domain": ""}])
    memory = tmp_path / "MEMORY.md"
    memory.write_text("## Lessons Learned\n- a lesson that was archived\n- a brand new lesson\n", encoding="utf-8")
    import_from_openclaw(engram, None, str(memory), None)
    summaries = [r["summary"] for r in engram.get_lessons(limit=None, _update_access=False)]
    assert summaries == ["a brand new lesson"]


def test_an_onboard_candidate_in_the_archive_counts_as_existing(engram: Engram):
    anchor = {"kind": "file", "ref": "README.md", "detail": {"size": 1}}
    first = engram.create_onboard_candidates([anchor], repo_id="repo-1")
    candidate_id = first["candidates"][0]["id"]
    path = engram._knowledge_dir / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [r for r in rows if r["id"] != candidate_id])
    again = engram.create_onboard_candidates([dict(anchor, detail={"size": 2})], repo_id="repo-1")
    assert (again["created"], again["existing"], again["updated"]) == (0, 1, 0)
    assert _active_ids(engram, "lesson") == []


def test_onboarding_runs_as_one_batch(engram: Engram, monkeypatch):
    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "1", "ENGRAM_REVIEW_MIN_STAY_DAYS": "0"}.items():
        monkeypatch.setenv(name, value)
    anchors = [{"kind": "file", "ref": f"file-{i}.md", "detail": {}} for i in range(3)]
    result = engram.create_onboard_candidates(anchors, repo_id="repo-1")
    assert len(result["overflow_archived_ids"]) == 2


def test_reconcile_matches_archived_rows_by_exact_text_only(engram: Engram):
    from piia_engram.reconcile_apply import apply_reconcile

    _archive(engram, "lesson", [{"id": "L-arch", "summary": "retry flaky network calls three times",
                                 "domain": "", "status": "active"}])
    result = apply_reconcile(engram, [
        {"summary": "retry flaky network calls three times"},
        {"summary": "retry flaky network calls three times with jitter"},
    ], dry_run=True)
    actions = [item["action"] for item in result["items"]]
    assert actions == ["duplicate", "import"]


# -- read budget: relevance over every visible lesson ----------------------------------


def test_relevant_lessons_rank_every_visible_lesson_and_count_only_returned_reads(engram: Engram):
    from knowledge_seed import raw_write_json

    rows = [{"id": "L-old-arch", "summary": "oldest architecture lesson", "domain": "架构", "tier": "verified",
             "status": "active", "timestamp": "2026-01-01T00:00:00Z"}]
    rows += [{"id": f"L-{i:03d}", "summary": f"filler lesson {i}", "domain": "misc", "tier": "verified",
              "status": "active", "timestamp": f"2026-02-01T00:{i // 60:02d}:{i % 60:02d}Z"} for i in range(210)]
    raw_write_json(engram._knowledge_dir / "lessons.json", rows)
    picked = engram.get_relevant_lessons(limit=8)
    assert "L-old-arch" in [r["id"] for r in picked]
    stored = {r["id"]: r for r in engram._read_entries(engram._knowledge_dir / "lessons.json", "lesson",
                                                        migrate=False)}
    counted = {i for i, r in stored.items() if r.get("access_count", 0) > 0}
    assert counted == {r["id"] for r in picked}
