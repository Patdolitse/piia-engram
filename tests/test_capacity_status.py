"""Counts per pool, the read-only retention plan, doctor, weekly and backup-plan (v4.21)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram.storage import _append_jsonl_lines


@pytest.fixture()
def engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    for name, value in {"ENGRAM_REVIEW_QUEUE_MAX": "2", "ENGRAM_REVIEW_MIN_STAY_DAYS": "7"}.items():
        monkeypatch.setenv(name, value)
    return Engram(root=tmp_path / "store")


def _seed(engram: Engram) -> dict:
    from knowledge_seed import raw_write_json

    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        {"id": "L-v", "summary": "reviewed", "tier": "verified", "status": "active"},
        {"id": "L-q1", "summary": "queued one", "tier": "staging", "status": "active", "created_at": old},
        {"id": "L-q2", "summary": "queued two", "tier": "staging", "status": "active", "created_at": old},
        {"id": "L-q3", "summary": "queued three", "tier": "staging", "status": "active", "created_at": old},
        {"id": "L-r", "summary": "retired", "tier": "verified", "status": "outdated"},
        {"id": "L-future", "summary": "from the future", "tier": "verified", "status": "active",
         "created_at": "2999-01-01T00:00:00Z"},
    ]
    raw_write_json(engram._knowledge_dir / "lessons.json", rows)
    _append_jsonl_lines(engram._overflow_archive_path("lesson"), [
        json.dumps({"id": "L-a1", "summary": "archived", "overflow_archive_reason": "review_queue_quota",
                    "overflow_archived_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}),
        '{"id": "torn',
    ])
    return {"old": old}


def test_capacity_status_counts_pools_and_previews_the_next_moves(engram: Engram):
    _seed(engram)
    status = engram.capacity_status()
    lessons = status["kinds"]["lesson"]
    assert (lessons["verified"], lessons["queued"], lessons["demoted"], lessons["retired"]) == (2, 3, 0, 1)
    assert lessons["archived"] == 1
    assert lessons["archived_by_reason"] == {"review_queue_quota": 1}
    assert lessons["archive_torn_lines"] == 1
    assert lessons["future_timestamps"] == 1
    assert lessons["next_moves"] == [{"id": "L-q1", "reason": "review_queue_quota"}]
    assert status["limits"]["hard_cap"] == 1000
    assert status["import_pending"] is False
    assert json.loads((engram._knowledge_dir / "lessons.json").read_text(encoding="utf-8"))[1]["id"] == "L-q1"


def test_capacity_status_reports_an_interrupted_import(engram: Engram):
    (engram._knowledge_dir).mkdir(parents=True, exist_ok=True)
    (engram._knowledge_dir / ".import-pending.json").write_text("{}", encoding="utf-8")
    assert engram.capacity_status()["import_pending"] is True


def test_retention_plan_cli_prints_counts_and_writes_nothing(engram: Engram, monkeypatch, capsys):
    from piia_engram.cli_commands import _run_retention

    _seed(engram)
    monkeypatch.setenv("ENGRAM_DIR", str(engram.root))
    before = (engram._knowledge_dir / "lessons.json").read_bytes()
    assert _run_retention(["plan"]) == 0
    text = capsys.readouterr().out
    assert "lesson: verified=2 queued=3 demoted=0 retired=1 archived=1" in text
    assert "next pass would move 1 (review_queue_quota=1)" in text
    assert "future timestamps: 1" in text
    assert _run_retention(["plan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kinds"]["lesson"]["queued"] == 3
    assert (engram._knowledge_dir / "lessons.json").read_bytes() == before


def test_retention_restore_cli_brings_a_row_back(engram: Engram, monkeypatch, capsys):
    from piia_engram.cli_commands import _run_retention

    lesson = engram.add_lesson({"summary": "a lesson that gets archived", "domain": "x", "tier": "staging"})
    path = engram._knowledge_dir / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [r for r in rows if r["id"] != lesson["id"]])
    monkeypatch.setenv("ENGRAM_DIR", str(engram.root))
    assert _run_retention(["restore", lesson["id"]]) == 0
    assert "restored" in capsys.readouterr().out
    assert [r["id"] for r in Engram(root=engram.root).get_lessons(limit=None, _update_access=False)] == [lesson["id"]]
    assert _run_retention(["restore", "no-such-id"]) == 1


def test_the_mcp_doctor_reports_capacity(engram: Engram, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    _seed(engram)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    report = json.loads(asyncio.run(mcp_server.doctor(output_format="json")))
    check = next(c for c in report["checks"] if c["name"] == "capacity")
    assert check["status"] == "WARN"
    assert "lesson: verified=2/1000 queued=3/2 retired=1 archived=1" in check["detail"]
    assert "future timestamps" in check["detail"]


def test_the_crowded_warning_looks_at_reviewed_rows_against_the_hard_cap(engram: Engram, monkeypatch):
    from knowledge_seed import raw_write_json

    monkeypatch.setenv("ENGRAM_CAP_SOFT", "3")
    monkeypatch.setenv("ENGRAM_CAP_HARD", "4")
    rows = [{"id": f"L-{i}", "summary": f"reviewed number {i}", "tier": "verified", "status": "active"}
            for i in range(4)]
    raw_write_json(engram._knowledge_dir / "lessons.json", rows)
    report = engram.get_health_report()
    assert any("4/4" in w for w in report["warnings"])
    assert report["capacity"]["lesson"] == {"verified": 4, "queued": 0, "demoted": 0, "retired": 0,
                                            "archived": 0}


def test_weekly_recap_counts_archive_moves(engram: Engram):
    from piia_engram.reports_weekly import build_weekly_recap, render_weekly_text

    _seed(engram)
    recap = build_weekly_recap(engram)
    assert recap["capacity"] == {"moved_to_archive": {"review_queue_quota": 1}, "placed_in_archive": 0,
                                 "queue_remaining": 3, "demoted": 0}
    assert "moved to the overflow archive: 1" in render_weekly_text(recap)


def test_backup_plan_counts_parseable_and_torn_archive_lines(engram: Engram):
    from piia_engram.recovery import build_backup_plan

    _seed(engram)
    plan = build_backup_plan(engram.root)
    archive = next(d for d in plan["knowledge_datasets"] if d["dataset"] == "lessons_overflow_archive")
    assert (archive["entries"], archive["torn_lines"]) == (1, 1)


def test_a_withheld_write_ack_keeps_the_archive_count():
    from piia_engram import governance_runtime

    out = governance_runtime._withheld_write_ack(
        {"id": "x", "summary": "secret", "overflow_archived_ids": ["a", "b"], "placement": "archived"},
        tool="memory_store", trust="agent",
    )
    assert out["overflow_archived_count"] == 2
    assert out["placement"] == "archived"
    assert "summary" not in out and "overflow_archived_ids" not in out
