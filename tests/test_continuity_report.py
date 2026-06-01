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
        "recall_signals",
        "readiness_checks",
        "readiness_level",
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
    assert set(report["recall_signals"]) == {
        "observed",
        "context_load_calls",
        "wrap_up_calls",
        "telemetry",
        "beta_events",
    }
    assert set(report["recall_signals"]["telemetry"]) == {
        "present",
        "context_load_calls",
        "wrap_up_calls",
    }
    assert set(report["recall_signals"]["beta_events"]) == {
        "present",
        "cold_start_events",
        "session_end_events",
    }
    assert set(report["readiness_checks"]) == {
        "has_saved_sessions",
        "has_multiple_tools",
        "resume_brief_builds",
        "has_context_load_signal",
        "has_wrap_up_signal",
    }
    assert report["readiness_level"] in {
        "not_ready",
        "single_tool_ready",
        "cross_tool_ready",
        "observed_signals",
    }
    assert set(report["project"]) == {"provided", "name"}


def test_continuity_readiness_level_mapping() -> None:
    from piia_engram.continuity_report import READINESS_LEVELS, _readiness_level

    assert READINESS_LEVELS == {
        "not_ready",
        "single_tool_ready",
        "cross_tool_ready",
        "observed_signals",
    }

    base = {
        "has_saved_sessions": False,
        "has_multiple_tools": False,
        "resume_brief_builds": False,
        "has_context_load_signal": False,
        "has_wrap_up_signal": False,
    }

    assert _readiness_level(base) == "not_ready"
    assert _readiness_level({**base, "has_saved_sessions": True}) == "single_tool_ready"
    assert (
        _readiness_level(
            {
                **base,
                "has_saved_sessions": True,
                "has_multiple_tools": True,
                "resume_brief_builds": True,
            }
        )
        == "cross_tool_ready"
    )
    assert (
        _readiness_level(
            {
                **base,
                "has_saved_sessions": True,
                "has_multiple_tools": True,
                "resume_brief_builds": True,
                "has_context_load_signal": True,
                "has_wrap_up_signal": True,
            }
        )
        == "observed_signals"
    )
    assert _readiness_level(base) in READINESS_LEVELS


def test_continuity_report_includes_recall_signals_without_payload_leak(
    tmp_path: Path,
) -> None:
    from piia_engram.continuity_report import (
        build_continuity_report,
        render_continuity_text,
    )

    secret = "ZZ_RECALL_SIGNAL_SECRET"
    (tmp_path / "telemetry.log").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tool_calls": {
                            "get_user_context": {"success": 2, "error": 1},
                            "get_resume_brief": {"success": 4, "error": 0},
                            "wrap_up_session": {"success": 3, "error": 0},
                            secret: {"success": 99, "error": 0},
                        }
                    }
                ),
                "{not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "beta_events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "cold_start", "d": {"level": secret}}),
                json.dumps({"event": "session_end", "d": {"source_tool": secret}}),
                json.dumps({"event": "knowledge_created", "d": {"summary": secret}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_continuity_report(Engram(root=tmp_path), project_folder=str(tmp_path))
    rendered = render_continuity_text(report)
    payload = json.dumps(report, ensure_ascii=False)

    assert report["recall_signals"]["observed"] is True
    assert report["recall_signals"]["context_load_calls"] == 7
    assert report["recall_signals"]["wrap_up_calls"] == 3
    assert report["recall_signals"]["telemetry"] == {
        "present": True,
        "context_load_calls": 7,
        "wrap_up_calls": 3,
    }
    assert report["recall_signals"]["beta_events"] == {
        "present": True,
        "cold_start_events": 1,
        "session_end_events": 1,
    }
    assert "Context load signals" in rendered
    assert "Wrap-up signals" in rendered
    assert secret not in payload
    assert secret not in rendered
    assert str(tmp_path) not in payload
    assert str(tmp_path) not in rendered


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
    assert report["recall_signals"]["observed"] is False
    assert "No saved sessions yet" in rendered
