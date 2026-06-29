"""Project memory boundary hardening.

Local Engram can be a private memory layer, but project-specific memories must
not bleed across projects by default. Project-scoped reads should include
matching project items plus global reusable items, and exclude other projects.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path / "store")


def test_project_scoped_lesson_write_is_filtered_from_other_project_search(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    eng.add_lesson(
        {"summary": "Project A private build rule", "project_folder": str(project_a)},
        domain="boundary",
    )
    eng.add_lesson(
        {"summary": "Reusable cross project lesson"},
        domain="boundary",
    )

    a_results = eng.search_knowledge("project lesson", scope="lessons", project_folder=str(project_a))
    b_results = eng.search_knowledge("project lesson", scope="lessons", project_folder=str(project_b))
    global_results = eng.search_knowledge("project lesson", scope="lessons")

    assert "Project A private build rule" in _lesson_summaries(a_results)
    assert "Reusable cross project lesson" in _lesson_summaries(a_results)
    assert "Project A private build rule" not in _lesson_summaries(b_results)
    assert "Reusable cross project lesson" in _lesson_summaries(b_results)
    assert "Project A private build rule" not in _lesson_summaries(global_results)


def test_project_scoped_decision_write_is_filtered_from_other_project_reads(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    eng.add_decision(
        {
            "question": "Project A storage choice",
            "choice": "local only",
            "project_folder": str(project_a),
        }
    )
    eng.add_decision("Reusable decision question", "global choice")

    a_decisions = eng.get_decisions(project_folder=str(project_a), limit=None)
    b_decisions = eng.get_decisions(project_folder=str(project_b), limit=None)
    global_decisions = eng.get_decisions(limit=None)

    assert "Project A storage choice" in _decision_questions(a_decisions)
    assert "Reusable decision question" in _decision_questions(a_decisions)
    assert "Project A storage choice" not in _decision_questions(b_decisions)
    assert "Reusable decision question" in _decision_questions(b_decisions)
    assert "Project A storage choice" not in _decision_questions(global_decisions)


def test_resume_brief_uses_project_scoped_recent_context_and_knowledge(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    eng.save_project_snapshot(str(project_a), {"title": "Project A"})
    eng.save_project_snapshot(str(project_b), {"title": "Project B"})
    eng.add_lesson(
        {"summary": "Project A resume lesson", "project_folder": str(project_a), "tier": "verified"}
    )
    eng.add_lesson(
        {"summary": "Project B resume lesson", "project_folder": str(project_b), "tier": "verified"}
    )
    eng.save_agent_context(
        tool="codex",
        content="Project A checkpoint should appear.",
        session_id="a-session",
        project_folder=str(project_a),
    )
    eng.save_agent_context(
        tool="codex",
        content="Project B checkpoint must not appear.",
        session_id="b-session",
        project_folder=str(project_b),
    )

    brief = eng.get_resume_brief(project_folder=str(project_a), token_budget=3000)["markdown"]

    assert "Project A resume lesson" in brief
    assert "Project A checkpoint should appear" in brief
    assert "Project B resume lesson" not in brief
    assert "Project B checkpoint must not appear" not in brief


def test_global_recent_context_excludes_project_scoped_sessions(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_a.mkdir()

    eng.save_agent_context(
        tool="codex",
        content="Global checkpoint should appear.",
        session_id="global-session",
    )
    eng.save_agent_context(
        tool="codex",
        content="Project A checkpoint must not appear globally.",
        session_id="project-session",
        project_folder=str(project_a),
    )

    sessions = eng.get_recent_context(tool="codex", limit=5)
    contents = "\n".join(item["content"] for item in sessions)
    brief = eng.get_resume_brief(token_budget=3000)["markdown"]

    assert "Global checkpoint should appear." in contents
    assert "Project A checkpoint must not appear globally." not in contents
    assert "Global checkpoint should appear." in brief
    assert "Project A checkpoint must not appear globally." not in brief


def test_legacy_project_fields_are_filtered_from_other_project_and_global_reads(
    tmp_path: Path,
):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    eng.add_lesson({
        "summary": "Legacy source project lesson",
        "source_project": str(project_a),
        "tier": "verified",
    })
    eng.add_lesson({
        "summary": "Legacy label project lesson",
        "project": "project-a",
        "tier": "verified",
    })

    a_lessons = eng.get_lessons(project_folder=str(project_a), limit=None, _update_access=False)
    b_lessons = eng.get_lessons(project_folder=str(project_b), limit=None, _update_access=False)
    global_lessons = eng.get_lessons(limit=None, _update_access=False)

    assert "Legacy source project lesson" in _lesson_items(a_lessons)
    assert "Legacy label project lesson" in _lesson_items(a_lessons)
    assert "Legacy source project lesson" not in _lesson_items(b_lessons)
    assert "Legacy label project lesson" not in _lesson_items(b_lessons)
    assert "Legacy source project lesson" not in _lesson_items(global_lessons)
    assert "Legacy label project lesson" not in _lesson_items(global_lessons)


def test_deduplication_is_limited_to_matching_project_scope(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    first = eng.add_lesson({
        "summary": "Run boundary tests before shipping",
        "project_folder": str(project_a),
        "tier": "verified",
    })
    other_project = eng.add_lesson({
        "summary": "Run boundary tests before shipping",
        "project_folder": str(project_b),
        "tier": "verified",
    })
    same_project = eng.add_lesson({
        "summary": "Run boundary tests before shipping",
        "project_folder": str(project_a),
        "tier": "verified",
    })

    assert first.get("status") != "duplicate"
    assert other_project.get("status") != "duplicate"
    assert same_project.get("status") == "duplicate"
    assert same_project.get("existing_id") == first["id"]


def test_project_folder_write_does_not_persist_raw_path_as_public_label(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "private" / "project-a"
    project_a.mkdir(parents=True)

    lesson = eng.add_lesson(
        {"summary": "Project A private path rule", "project_folder": str(project_a)},
        tier="verified",
    )

    stored = next(
        item for item in eng.get_lessons(
            project_folder=str(project_a),
            limit=None,
            _update_access=False,
        )
        if item["id"] == lesson["id"]
    )
    assert stored["project_id"]
    assert stored.get("project") == "project-a"
    assert stored.get("source_project") != str(project_a)
    assert stored.get("provenance", {}).get("source_project") != str(project_a)
    assert "project_folder" not in stored


def test_project_folder_write_scrubs_nested_provenance_raw_path(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "private" / "project-a"
    project_a.mkdir(parents=True)
    raw_project = str(project_a)

    lesson = eng.add_lesson(
        {
            "summary": "Nested provenance path must be scrubbed",
            "project_folder": raw_project,
            "provenance": {
                "source_project": raw_project,
                "source_project_folder": raw_project,
                "project_folder": raw_project,
            },
        },
        tier="verified",
    )

    stored = next(
        item for item in eng.get_lessons(
            project_folder=raw_project,
            limit=None,
            _update_access=False,
        )
        if item["id"] == lesson["id"]
    )
    provenance = stored.get("provenance", {})
    assert provenance.get("project") == "project-a"
    assert provenance.get("project_id") == stored["project_id"]
    assert provenance.get("source_project") != raw_project
    assert provenance.get("source_project_folder") != raw_project
    assert provenance.get("project_folder") != raw_project


def test_explicit_decision_supersedes_is_limited_to_matching_project_scope(tmp_path: Path):
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    old_a = eng.add_decision({
        "question": "Project A cache backend",
        "choice": "sqlite",
        "project_folder": str(project_a),
        "tier": "verified",
    })
    new_b = eng.add_decision({
        "question": "Project B cache backend",
        "choice": "redis",
        "project_folder": str(project_b),
        "supersedes": old_a["id"],
        "tier": "verified",
    })
    new_a = eng.add_decision({
        "question": "Project A cache backend revision",
        "choice": "duckdb",
        "project_folder": str(project_a),
        "supersedes": old_a["id"],
        "tier": "verified",
    })

    edges = RelationStore(eng.root).all_edges()
    assert {"src": new_b["id"], "rel": "supersedes", "dst": old_a["id"]} not in edges
    assert {"src": new_a["id"], "rel": "supersedes", "dst": old_a["id"]} in edges


def test_hybrid_search_index_is_limited_to_visible_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _eng(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    eng.add_lesson(
        {"summary": "alpha visible hybrid lesson", "project_folder": str(project_a)}
    )
    eng.add_lesson(
        {"summary": "beta hidden hybrid lesson", "project_folder": str(project_b)}
    )

    results = eng.search_knowledge(
        "visible hybrid",
        scope="lessons",
        project_folder=str(project_a),
        allow_hybrid_index=True,
    )

    assert "alpha visible hybrid lesson" in _lesson_summaries(results)
    index_path = eng.root / "search_index.db"
    assert index_path.exists()
    with sqlite3.connect(index_path) as con:
        docs = "\n".join(row[0] for row in con.execute("SELECT doc FROM fts"))
    assert "visible" in docs
    assert "hidden" not in docs


def _lesson_summaries(results: dict) -> set[str]:
    return {str(item.get("summary")) for item in results.get("lessons", [])}


def _lesson_items(items: list[dict]) -> set[str]:
    return {str(item.get("summary")) for item in items}


def _decision_questions(items: list[dict]) -> set[str]:
    return {str(item.get("question") or item.get("title")) for item in items}
