"""M2B: project resume pack quality metadata and selection policy."""

from __future__ import annotations

import json
import os
from pathlib import Path

from piia_engram.core import Engram


_FAKE_KEY = "sk-" + "ABCDEF1234567890abcdef"


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _project(tmp_path: Path, name: str = "quality-proj") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _save_digest_session(eng: Engram, project: Path, *, session_id: str = "sess-1") -> None:
    eng.save_agent_context(
        "codex",
        (
            "Goal: finish continuity quality.\n"
            "Completed: added resume pack quality tests.\n"
            "Lesson: keep session-derived memory as review candidates.\n"
            "Decided to keep review candidates separate from trusted context.\n"
            "Next: implement pack metadata.\n"
        ),
        session_id=session_id,
        project_folder=str(project),
    )


def test_pack_meta_counts_match_output_arrays(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    eng.save_project_snapshot(str(project), {"title": "Quality Project", "stage": "M2"})
    _save_digest_session(eng, project)
    eng.add_lesson("Verified lesson for quality metadata", tier="verified")
    eng.add_decision("Verified decision for quality metadata", choice="Use pack_meta", tier="verified")
    eng.add_lesson("Staging lesson needs review", tier="staging")

    pack = eng.build_project_resume_pack(project_folder=str(project), digest_limit=1, knowledge_limit=2)

    meta = pack["pack_meta"]
    assert meta["digest_count"] == 1
    assert meta["trusted_count"] == len(pack["trusted_context"])
    assert meta["review_needed_count"] == len(pack["review_needed"])
    assert meta["omitted_count"] == len(pack["omitted"])
    assert meta["selection_policy"] == "recency_then_review_state_then_limit"
    assert meta["budget"] == {"digest_limit": 1, "knowledge_limit": 2}


def test_quality_signals_reflect_empty_partial_and_full_pack_states(tmp_path: Path):
    eng = _eng(tmp_path)
    empty_project = _project(tmp_path, "empty")
    empty = eng.build_project_resume_pack(project_folder=str(empty_project))
    assert empty["quality_signals"] == []

    snap_project = _project(tmp_path, "snapshot")
    eng.save_project_snapshot(str(snap_project), {"title": "Snapshot Project", "stage": "M2"})
    partial = eng.build_project_resume_pack(project_folder=str(snap_project))
    assert partial["quality_signals"] == ["has_project_snapshot"]

    _save_digest_session(eng, snap_project)
    eng.add_lesson("Staging candidate for signal", tier="staging")
    full = eng.build_project_resume_pack(project_folder=str(snap_project))
    assert "has_project_snapshot" in full["quality_signals"]
    assert "has_recent_digest" in full["quality_signals"]
    assert "has_next_action" in full["quality_signals"]
    assert "has_review_candidates" in full["quality_signals"]


def test_resume_pack_order_is_deterministic_across_repeated_calls(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    eng.save_project_snapshot(str(project), {"title": "Deterministic Project", "stage": "M2"})
    _save_digest_session(eng, project, session_id="sess-a")
    eng.add_lesson("Alpha verified lesson", tier="verified")
    eng.add_lesson("Beta verified lesson", tier="verified")
    eng.add_decision("Architecture choice", choice="Deterministic order", tier="verified")

    first = eng.build_project_resume_pack(project_folder=str(project), digest_limit=2, knowledge_limit=3)
    second = eng.build_project_resume_pack(project_folder=str(project), digest_limit=2, knowledge_limit=3)

    assert first == second
    assert first["handoff"]["current_focus"] == "implement pack metadata."
    assert [item["kind"] for item in first["trusted_context"][:4]] == [
        "project_snapshot",
        "lesson",
        "lesson",
        "decision",
    ]


def test_omitted_records_appear_when_knowledge_limit_is_exceeded(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    eng.save_project_snapshot(str(project), {"title": "Limit Project", "stage": "M2"})
    eng.add_lesson("Kept verified lesson", tier="verified")
    eng.add_lesson("Omitted verified lesson", tier="verified")
    eng.add_decision("Omitted decision", choice="Past the budget", tier="verified")

    pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=1)

    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    omitted = pack["omitted"]
    assert "Omitted verified lesson" in trusted
    assert "Kept verified lesson" not in trusted
    assert "Omitted decision" not in trusted
    assert {"kind": "lesson", "reason": "knowledge_limit", "source": "knowledge"} in omitted
    assert {"kind": "decision", "reason": "knowledge_limit", "source": "knowledge"} in omitted
    assert pack["pack_meta"]["omitted_count"] == len(omitted)


def test_review_candidates_never_enter_trusted_context(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    _save_digest_session(eng, project)
    eng.add_lesson("Staging lesson must stay review-needed", tier="staging")
    eng.add_decision("Staging decision", choice="Needs owner review", tier="staging")

    pack = eng.build_project_resume_pack(project_folder=str(project))
    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    review = json.dumps(pack["review_needed"], ensure_ascii=False)

    assert "Staging lesson must stay review-needed" in review
    assert "Staging decision" in review
    assert "session-derived memory" in review
    assert "Staging lesson must stay review-needed" not in trusted
    assert "Staging decision" not in trusted
    assert "session-derived memory" not in trusted


def test_archived_knowledge_never_enters_resume_trusted_or_review_needed(tmp_path: Path):
    eng = _eng(tmp_path)
    lesson = eng.add_lesson("Archived lesson must not reappear in handoff", tier="verified")
    decision = eng.add_decision(
        "Archived decision",
        choice="must not reappear in handoff",
        tier="verified",
    )
    assert eng.soft_archive_knowledge_tier(lesson["id"], allow_verified=True)["changed"] is True
    assert eng.soft_archive_knowledge_tier(decision["id"], allow_verified=True)["changed"] is True

    pack = eng.build_project_resume_pack()
    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    review = json.dumps(pack["review_needed"], ensure_ascii=False)

    assert "Archived lesson must not reappear in handoff" not in trusted
    assert "Archived decision" not in trusted
    assert "Archived lesson must not reappear in handoff" not in review
    assert "Archived decision" not in review


def test_sensitive_values_absent_from_meta_omitted_and_quality_signals(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    eng.save_project_snapshot(str(project), {"title": "Sensitive Project", "stage": "M2"})
    eng.add_lesson("Kept verified lesson", tier="verified")
    eng.add_lesson(
        f"Omitted lesson with {_FAKE_KEY}",
        tier="verified",
        source_tool="E:\\Private\\tool",
    )

    pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=1)
    blob = json.dumps(
        {
            "pack_meta": pack["pack_meta"],
            "omitted": pack["omitted"],
            "quality_signals": pack["quality_signals"],
        },
        ensure_ascii=False,
    )

    assert _FAKE_KEY not in blob
    assert "E:\\Private\\tool" not in blob
    assert str(tmp_path) not in blob


def test_project_scoped_knowledge_is_selected_for_matching_project(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path, "matching")
    other = _project(tmp_path, "other")
    eng.add_lesson("Global reusable lesson", tier="verified")
    eng.add_lesson("Matching project verified lesson", tier="verified", project_folder=str(project))
    eng.add_lesson("Other project verified lesson", tier="verified", project_folder=str(other))
    eng.add_lesson("Matching project staging lesson", tier="staging", project_folder=str(project))

    pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=4)
    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    review = json.dumps(pack["review_needed"], ensure_ascii=False)
    blob = trusted + review

    assert "Global reusable lesson" in trusted
    assert "Matching project verified lesson" in trusted
    assert "Matching project staging lesson" in review
    assert "Other project verified lesson" not in blob


def test_label_only_project_entries_match_project_name_and_stay_out_of_global_pack(
    tmp_path: Path,
):
    eng = _eng(tmp_path)
    project = _project(tmp_path, "labelled")
    other = _project(tmp_path, "other-label")
    eng.add_decision(
        "Label-only project decision",
        choice="Use project label",
        tier="verified",
        project=project.name,
    )
    eng.add_decision(
        "Other label-only project decision",
        choice="Do not leak",
        tier="verified",
        project=other.name,
    )

    project_pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=4)
    global_pack = eng.build_project_resume_pack(knowledge_limit=4)
    project_blob = json.dumps(project_pack["trusted_context"], ensure_ascii=False)
    global_blob = json.dumps(global_pack["trusted_context"], ensure_ascii=False)

    assert "Label-only project decision" in project_blob
    assert "Other label-only project decision" not in project_blob
    assert "Label-only project decision" not in global_blob
    assert "Other label-only project decision" not in global_blob


def test_digest_selection_filters_before_applying_limit(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path, "target")
    other = _project(tmp_path, "noise")
    _save_digest_session(eng, other, session_id="zzz-newer-noise")
    _save_digest_session(eng, project, session_id="aaa-target")
    base = 1_800_000_000
    os.utime(eng.root / "contexts" / "codex" / "zzz-newer-noise.md", (base + 10, base + 10))
    os.utime(eng.root / "contexts" / "codex" / "aaa-target.md", (base, base))

    pack = eng.build_project_resume_pack(project_folder=str(project), digest_limit=1)

    assert pack["pack_meta"]["digest_count"] == 1
    assert pack["handoff"]["current_focus"] == "implement pack metadata."
    assert any("quality tests" in item for item in pack["handoff"]["last_completed"])


def test_verified_knowledge_selection_prefers_recent_items(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    eng.add_lesson("Old verified lesson", tier="verified")
    eng.add_lesson("Recent verified lesson", tier="verified")

    pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=1)

    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    assert "Recent verified lesson" in trusted
    assert "Old verified lesson" not in trusted
    assert {"kind": "lesson", "reason": "knowledge_limit", "source": "knowledge"} in pack["omitted"]


def test_omitted_records_are_bounded_metadata(tmp_path: Path):
    eng = _eng(tmp_path)
    project = _project(tmp_path)
    for index in range(30):
        eng.add_lesson(f"Omitted lesson {index}", tier="verified")
    for index in range(30):
        eng.add_decision(f"Omitted decision {index}", choice="Past the budget", tier="verified")

    pack = eng.build_project_resume_pack(project_folder=str(project), knowledge_limit=0)

    assert len(pack["omitted"]) <= 8
    assert pack["omitted"] == [
        {"kind": "lesson", "reason": "knowledge_limit", "source": "knowledge"},
        {"kind": "decision", "reason": "knowledge_limit", "source": "knowledge"},
    ]
