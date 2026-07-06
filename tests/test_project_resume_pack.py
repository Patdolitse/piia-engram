"""Project resume pack v1 assembly.

The pack is a compact, structured handoff assembled from existing Engram
surfaces. It must stay zero-write, separate trusted memory from review-needed
items, and avoid raw session bodies.
"""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram

_FAKE_SK_KEY = "sk-" + "abcdef1234567890ABCDEF"


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def test_resume_pack_includes_digest_completed_and_next_actions(tmp_path: Path):
    eng = _eng(tmp_path)
    project = tmp_path / "pack-proj"
    project.mkdir()
    eng.save_project_snapshot(str(project), {"title": "Pack Project", "stage": "M1"})
    eng.save_agent_context(
        "codex",
        (
            "Goal: finish continuity handoff.\n"
            "Completed: wrote digest sidecar; added resume pack tests.\n"
            "Next: expose pack through get_resume_brief.\n"
        ),
        project_folder=str(project),
    )

    pack = eng.build_project_resume_pack(project_folder=str(project))

    assert pack["schema"] == "project_resume_pack.v1"
    assert pack["project"]["title"] == "Pack Project"
    assert pack["project"]["stage"] == "M1"
    assert "wrote digest sidecar" in pack["handoff"]["last_completed"]
    assert any(
        "expose pack through get_resume_brief" in item
        for item in pack["handoff"]["next_actions"]
    )
    assert "Context is reference" in pack["safety_notes"][0]


def test_resume_pack_separates_trusted_context_from_review_needed(tmp_path: Path):
    eng = _eng(tmp_path)
    eng.add_lesson("Verified lesson for handoff", domain="continuity", tier="verified")
    eng.add_decision("Verified decision", choice="Use sidecars", tier="verified")
    eng.add_lesson("Staging lesson needs owner review", domain="continuity", tier="staging")
    eng.save_agent_context(
        "claude_code",
        "Lesson: always keep session-derived memories as candidates.\n",
    )

    pack = eng.build_project_resume_pack()
    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    review = json.dumps(pack["review_needed"], ensure_ascii=False)

    assert "Verified lesson for handoff" in trusted
    assert "Verified decision" in trusted
    assert "Staging lesson needs owner review" in review
    assert "session-derived memories as candidates" in review
    assert "Staging lesson needs owner review" not in trusted


def test_resume_pack_redacts_sensitive_values_and_omits_raw_body(tmp_path: Path):
    eng = _eng(tmp_path)
    project = tmp_path / "secret-proj"
    project.mkdir()
    eng.save_agent_context(
        "cursor",
        (
            f"Goal: rotate key {_FAKE_SK_KEY}.\n"
            "Completed: checked E:\\Private\\store.db.\n"
            "Next: continue safely.\n"
        ),
        project_folder=str(project),
    )

    pack = eng.build_project_resume_pack(project_folder=str(project))
    blob = json.dumps(pack, ensure_ascii=False)

    assert _FAKE_SK_KEY not in blob
    assert "E:\\Private\\store.db" not in blob
    assert "raw_session" not in blob
    assert "content" not in blob


def test_resume_pack_remains_useful_without_project_snapshot(tmp_path: Path):
    eng = _eng(tmp_path)
    project = tmp_path / "no-snapshot"
    project.mkdir()

    pack = eng.build_project_resume_pack(project_folder=str(project))

    assert pack["schema"] == "project_resume_pack.v1"
    assert pack["project"]["title"] == "no-snapshot"
    assert pack["handoff"]["current_focus"]
    assert pack["trusted_context"] == []


def test_get_resume_brief_opt_in_adds_pack_without_changing_default(tmp_path: Path):
    eng = _eng(tmp_path)
    default = eng.get_resume_brief()
    opted = eng.get_resume_brief(include_resume_pack=True)

    assert "resume_pack" not in default
    assert opted["resume_pack"]["schema"] == "project_resume_pack.v1"
    assert "project_resume_pack" in opted["sections_included"]
