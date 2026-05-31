"""Continuity proof report tests."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram


def test_continuity_report_is_metadata_only(tmp_path: Path) -> None:
    from piia_engram.continuity_report import (
        build_continuity_report,
        render_continuity_text,
    )

    secret = "ZZ_CONTINUITY_SECRET_BODY"
    eng = Engram(root=tmp_path)
    eng.save_agent_context(
        tool="claude_code",
        content=f"handoff body {secret}",
        session_id="claude-secret-session",
        project_folder=str(tmp_path),
    )
    eng.save_agent_context(
        tool="codex",
        content=f"resume body {secret}",
        session_id="codex-secret-session",
        project_folder=str(tmp_path),
    )
    eng.add_lesson(secret, detail=f"private detail {secret}", domain="security")
    eng.add_decision(
        "private decision",
        choice=f"private choice {secret}",
        reasoning=f"private reason {secret}",
    )

    report = build_continuity_report(eng, project_folder=str(tmp_path))
    rendered = render_continuity_text(report)
    payload = json.dumps(report, ensure_ascii=False)

    assert report["session_count"] == 2
    assert report["tool_count"] == 2
    assert report["cross_tool_ready"] is True
    assert report["resume_brief"]["builds"] is True
    assert "recent_context" in report["resume_brief"]["sections_included"]
    assert "claude_code" in rendered
    assert "codex" in rendered
    assert secret not in rendered
    assert secret not in payload
    assert str(tmp_path) not in rendered
    assert str(tmp_path) not in payload
    assert "claude-secret-session" not in rendered
    assert "codex-secret-session" not in rendered


def test_continuity_report_schema_is_explicit_allowlist(tmp_path: Path) -> None:
    from piia_engram.continuity_report import build_continuity_report

    eng = Engram(root=tmp_path)
    eng.save_agent_context(tool="codex", content="checkpoint", session_id="s1")

    report = build_continuity_report(eng, project_folder=str(tmp_path))

    assert set(report) == {
        "schema",
        "verdict",
        "session_count",
        "tool_count",
        "tools",
        "sessions_by_tool",
        "cross_tool_ready",
        "latest",
        "resume_brief",
        "project",
    }
    assert set(report["latest"]) == {"tool", "modified_at", "size_bytes"}
    assert set(report["resume_brief"]) == {
        "builds",
        "sections_included",
        "sections_skipped",
        "byte_size",
        "estimated_tokens",
        "suggested_doc_count",
    }
    assert set(report["project"]) == {"provided", "name"}


def test_continuity_report_empty_state_is_informational(tmp_path: Path) -> None:
    from piia_engram.continuity_report import (
        build_continuity_report,
        render_continuity_text,
    )

    report = build_continuity_report(Engram(root=tmp_path), project_folder=str(tmp_path))
    rendered = render_continuity_text(report)

    assert report["session_count"] == 0
    assert report["tool_count"] == 0
    assert report["cross_tool_ready"] is False
    assert report["resume_brief"]["builds"] is True
    assert "No saved sessions yet" in rendered
