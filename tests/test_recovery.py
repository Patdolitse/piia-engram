import json
import os
from pathlib import Path

import pytest

from piia_engram.recovery import (
    analyze_json_recovery_candidates,
    analyze_recovery_retention_plan,
    write_recovery_candidate,
)


def _write_lessons(root: Path) -> tuple[Path, Path]:
    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True)
    active = knowledge / "lessons.json"
    active.write_bytes(b"\xef\xbb\xbf[]\r\n")
    backup = knowledge / "lessons.corrupt.20260531_010203.json"
    backup.write_text(
        json.dumps(
            [
                {
                    "id": "l1",
                    "summary": "SECRET_SUMMARY_TOKEN",
                    "detail": "SECRET_DETAIL_TOKEN",
                    "tier": "verified",
                    "sensitivity": "secret",
                    "created_at": "2026-05-30T10:00:00",
                    "source_tool": "codex",
                },
                {
                    "id": "l2",
                    "summary": "SECOND_SECRET_SUMMARY",
                    "detail": "SECOND_SECRET_DETAIL",
                    "tier": "staging",
                    "created_at": "2026-05-31T12:00:00",
                    "source_tool": "claude_code",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return active, backup


def test_analyze_json_recovery_candidates_redacts_content(tmp_path):
    _write_lessons(tmp_path)

    report = analyze_json_recovery_candidates(tmp_path, dataset="lessons")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert report["dataset"] == "lessons"
    assert report["best_candidate"]["file_name"] == "lessons.corrupt.20260531_010203.json"
    assert report["best_candidate"]["entries"] == 2
    assert report["active"]["entries"] == 0
    assert report["active"]["starts_bom"] is True
    assert report["best_candidate"]["date_min"] == "2026-05-30T10:00:00"
    assert report["best_candidate"]["date_max"] == "2026-05-31T12:00:00"
    assert report["best_candidate"]["content_keys_present"] == ["detail", "summary"]
    assert "SECRET_SUMMARY_TOKEN" not in serialized
    assert "SECRET_DETAIL_TOKEN" not in serialized
    assert "SECOND_SECRET" not in serialized


def test_write_recovery_candidate_requires_explicit_destination(tmp_path):
    active, _backup = _write_lessons(tmp_path)
    before = active.read_bytes()
    destination = tmp_path / "candidate" / "lessons.recovered.json"

    result = write_recovery_candidate(
        tmp_path,
        dataset="lessons",
        output_path=destination,
    )

    assert result["output_path"] == str(destination)
    assert result["entries"] == 2
    assert active.read_bytes() == before
    recovered = json.loads(destination.read_text(encoding="utf-8"))
    assert [item["id"] for item in recovered] == ["l1", "l2"]


def test_write_recovery_candidate_refuses_live_store_path(tmp_path):
    active, _backup = _write_lessons(tmp_path)
    before = active.read_bytes()

    with pytest.raises(RuntimeError, match="live Engram store"):
        write_recovery_candidate(
            tmp_path,
            dataset="lessons",
            output_path=active,
        )

    assert active.read_bytes() == before


def test_write_recovery_candidate_refuses_hardlink_to_live_store(tmp_path):
    active, _backup = _write_lessons(tmp_path)
    alias = tmp_path / "lessons-alias.json"
    try:
        os.link(active, alias)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks are unavailable in this environment: {exc}")
    before = active.read_bytes()

    with pytest.raises(RuntimeError, match="live Engram store"):
        write_recovery_candidate(
            tmp_path,
            dataset="lessons",
            output_path=alias,
        )

    assert active.read_bytes() == before


def test_write_recovery_candidate_refuses_symlink_to_live_store(tmp_path):
    active, _backup = _write_lessons(tmp_path)
    alias = tmp_path / "lessons-symlink.json"
    try:
        alias.symlink_to(active)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable in this environment: {exc}")
    before = active.read_bytes()

    with pytest.raises(RuntimeError, match="live Engram store"):
        write_recovery_candidate(
            tmp_path,
            dataset="lessons",
            output_path=alias,
        )

    assert active.read_bytes() == before


def test_write_recovery_candidate_refuses_existing_output_file(tmp_path):
    active, _backup = _write_lessons(tmp_path)
    destination = tmp_path / "candidate" / "lessons.recovered.json"
    destination.parent.mkdir()
    destination.write_text("do not replace", encoding="utf-8")
    before_active = active.read_bytes()
    before_destination = destination.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="existing output file"):
        write_recovery_candidate(
            tmp_path,
            dataset="lessons",
            output_path=destination,
        )

    assert active.read_bytes() == before_active
    assert destination.read_text(encoding="utf-8") == before_destination


@pytest.mark.parametrize("dataset", ["../lessons", "..\\lessons", "lessons*", ""])
def test_analyze_json_recovery_candidates_rejects_invalid_dataset_names(tmp_path, dataset):
    with pytest.raises(ValueError, match="invalid dataset name"):
        analyze_json_recovery_candidates(tmp_path, dataset=dataset)


def test_analyze_json_recovery_candidates_ignores_non_record_lists(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "lessons.corrupt.20260531_010203.json").write_text(
        json.dumps(["not-a-record"] * 10),
        encoding="utf-8",
    )
    (knowledge / "lessons.corrupt.20260531_020304.json").write_text(
        json.dumps([
            {
                "id": "l1",
                "summary": "SECRET_SUMMARY_TOKEN",
                "created_at": "2026-05-31T02:03:04",
            }
        ]),
        encoding="utf-8",
    )

    report = analyze_json_recovery_candidates(tmp_path, dataset="lessons")

    assert report["best_candidate"]["file_name"] == "lessons.corrupt.20260531_020304.json"


def test_analyze_json_recovery_candidates_ignores_foreign_id_records(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "lessons.corrupt.20260531_010203.json").write_text(
        json.dumps([
            {"id": f"foreign-{index}", "name": "not an Engram lesson"}
            for index in range(10)
        ]),
        encoding="utf-8",
    )
    (knowledge / "lessons.corrupt.20260531_020304.json").write_text(
        json.dumps([
            {
                "id": "l1",
                "summary": "SECRET_SUMMARY_TOKEN",
                "created_at": "2026-05-31T02:03:04",
                "tier": "verified",
            }
        ]),
        encoding="utf-8",
    )

    report = analyze_json_recovery_candidates(tmp_path, dataset="lessons")

    assert report["candidate_count"] == 1
    assert report["best_candidate"]["file_name"] == "lessons.corrupt.20260531_020304.json"


def test_analyze_json_recovery_candidates_prefers_newer_engram_candidate_over_extra_fields(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "lessons.corrupt.20260530_010203.json").write_text(
        json.dumps([
            {
                "id": "old",
                "summary": "SECRET_SUMMARY_TOKEN",
                "created_at": "2026-05-30T01:02:03",
                "tier": "verified",
                "archived_at": "2026-05-30T02:02:03",
                "promoted_at": "2026-05-30T03:02:03",
                "promotion_reason": "schema-rich older candidate",
            }
        ]),
        encoding="utf-8",
    )
    (knowledge / "lessons.corrupt.20260531_020304.json").write_text(
        json.dumps([
            {
                "id": "new",
                "summary": "SECRET_SUMMARY_TOKEN",
                "created_at": "2026-05-31T02:03:04",
                "tier": "verified",
            }
        ]),
        encoding="utf-8",
    )

    report = analyze_json_recovery_candidates(tmp_path, dataset="lessons")

    assert report["best_candidate"]["file_name"] == "lessons.corrupt.20260531_020304.json"


def test_analyze_json_recovery_candidates_handles_mixed_timezone_dates(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "lessons.corrupt.20260531_010203.json").write_text(
        json.dumps([
            {"id": "l1", "summary": "SECRET_SUMMARY_TOKEN", "created_at": "2026-05-30T10:00:00Z"},
            {"id": "l2", "summary": "SECRET_SUMMARY_TOKEN", "created_at": "2026-05-31T12:00:00"},
        ]),
        encoding="utf-8",
    )

    report = analyze_json_recovery_candidates(tmp_path, dataset="lessons")

    assert report["best_candidate"]["date_min"] == "2026-05-30T10:00:00Z"
    assert report["best_candidate"]["date_max"] == "2026-05-31T12:00:00"


def test_recovery_retention_plan_is_metadata_only_and_detects_overflow(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_bytes(b"\xef\xbb\xbf[]\r\n")
    older = knowledge / "lessons.corrupt.20260528_010203.json"
    newer = knowledge / "lessons.corrupt.20260531_010203.json"
    older.write_text(
        json.dumps([
            {
                "id": "shared",
                "summary": "OLDER_SHARED_SECRET",
                "detail": "OLDER_SHARED_DETAIL",
                "tier": "verified",
                "access_count": 1,
                "last_reviewed": "2026-05-28T01:02:03",
                "created_at": "2026-05-28T01:02:03",
            },
            {
                "id": "old-archived",
                "summary": "OLD_ARCHIVED_SECRET",
                "tier": "archived",
                "created_at": "2026-05-27T01:02:03",
            },
            {
                "id": "old-only",
                "summary": "OLD_ONLY_SECRET",
                "tier": "verified",
                "created_at": "2026-05-27T02:02:03",
            },
        ]),
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps([
            {
                "id": "shared",
                "summary": "NEWER_SHARED_SECRET",
                "detail": "NEWER_SHARED_DETAIL",
                "tier": "verified",
                "access_count": 3,
                "last_reviewed": "2026-05-31T01:02:03",
                "created_at": "2026-05-28T01:02:03",
            },
            {
                "id": "new-only-a",
                "summary": "NEW_ONLY_A_SECRET",
                "tier": "verified",
                "created_at": "2026-05-31T01:02:03",
            },
            {
                "id": "new-only-b",
                "summary": "NEW_ONLY_B_SECRET",
                "tier": "verified",
                "created_at": "2026-05-31T02:02:03",
            },
        ]),
        encoding="utf-8",
    )

    plan = analyze_recovery_retention_plan(tmp_path, dataset="lessons", max_entries=4)
    serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True)

    assert plan["primary_candidate"]["file_name"] == newer.name
    assert plan["secondary_candidate"]["file_name"] == older.name
    assert plan["overlap_ids"] == 1
    assert plan["primary_only_ids"] == 2
    assert plan["secondary_only_ids"] == 2
    assert plan["union_ids"] == 5
    assert plan["overflow_ids"] == 1
    assert plan["active_merge_safe"] is False
    assert plan["secondary_only_archived"] == 1
    assert plan["recommendation"] == "restore_primary_preserve_secondary_overflow"
    assert plan["live_store_modified"] is False
    assert "SECRET" not in serialized
    assert "shared" not in serialized


def test_recovery_retention_plan_reports_safe_active_merge_when_under_cap(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "lessons.json").write_text("[]", encoding="utf-8")
    (knowledge / "lessons.corrupt.20260528_010203.json").write_text(
        json.dumps([{"id": "old", "summary": "OLD_SECRET", "tier": "verified"}]),
        encoding="utf-8",
    )
    (knowledge / "lessons.corrupt.20260531_010203.json").write_text(
        json.dumps([{"id": "new", "summary": "NEW_SECRET", "tier": "verified"}]),
        encoding="utf-8",
    )

    plan = analyze_recovery_retention_plan(tmp_path, dataset="lessons", max_entries=5)

    assert plan["union_ids"] == 2
    assert plan["overflow_ids"] == 0
    assert plan["active_merge_safe"] is True
    assert plan["recommendation"] == "active_merge_possible_with_human_review"
