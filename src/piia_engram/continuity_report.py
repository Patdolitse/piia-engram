"""Metadata-only continuity proof for cross-tool handoff."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def build_continuity_report(
    eng,
    *,
    project_folder: str = "",
    session_limit: int = 500,
    token_budget: int = 1200,
) -> dict[str, Any]:
    """Build a shareable continuity proof without memory bodies or paths."""
    project_folder = str(project_folder or "")
    sessions = eng.list_agent_sessions(limit=max(1, int(session_limit)))
    tool_counts = Counter(str(s.get("tool") or "unknown") for s in sessions)
    tools = sorted(tool_counts)
    latest = sessions[0] if sessions else None

    try:
        brief = eng.get_resume_brief(
            project_folder=project_folder,
            token_budget=token_budget,
        )
        brief_builds = bool(brief.get("markdown"))
        brief_meta = {
            "builds": brief_builds,
            "sections_included": list(brief.get("sections_included") or []),
            "sections_skipped": list(brief.get("sections_skipped") or []),
            "byte_size": int(brief.get("byte_size") or 0),
            "estimated_tokens": int(brief.get("estimated_tokens") or 0),
            "suggested_doc_count": len(brief.get("suggested_docs") or []),
        }
    except Exception as exc:
        brief_meta = {
            "builds": False,
            "sections_included": [],
            "sections_skipped": [type(exc).__name__],
            "byte_size": 0,
            "estimated_tokens": 0,
            "suggested_doc_count": 0,
        }

    session_count = len(sessions)
    tool_count = len(tools)
    cross_tool_ready = tool_count >= 2
    if cross_tool_ready and brief_meta["builds"]:
        verdict = "ready"
    elif session_count and brief_meta["builds"]:
        verdict = "single-tool"
    elif brief_meta["builds"]:
        verdict = "no-session"
    else:
        verdict = "blocked"

    return {
        "schema": 1,
        "verdict": verdict,
        "session_count": session_count,
        "tool_count": tool_count,
        "tools": tools,
        "sessions_by_tool": dict(sorted(tool_counts.items())),
        "cross_tool_ready": cross_tool_ready,
        "latest": {
            "tool": latest.get("tool") if latest else None,
            "modified_at": latest.get("modified_at") if latest else None,
            "size_bytes": latest.get("size_bytes") if latest else None,
        },
        "resume_brief": brief_meta,
        "project": {
            "provided": bool(project_folder),
            "name": Path(project_folder).name if project_folder else "",
        },
    }


def render_continuity_text(report: dict[str, Any]) -> str:
    """Render continuity proof as concise metadata-only text."""
    session_count = int(report.get("session_count") or 0)
    tool_count = int(report.get("tool_count") or 0)
    tools = list(report.get("tools") or [])
    brief = report.get("resume_brief") or {}
    mark = "ok" if report.get("cross_tool_ready") else "--"
    session_word = "session" if session_count == 1 else "sessions"

    lines = [
        "Engram continuity proof",
        f"  [{mark}] Sessions: {session_count} saved {session_word} across {tool_count} tool(s)",
    ]
    if tools:
        lines.append(f"       Tools: {', '.join(str(t) for t in tools)}")
    else:
        lines.append("       No saved sessions yet")

    if brief.get("builds"):
        sections = ", ".join(str(s) for s in brief.get("sections_included") or [])
        lines.append(f"  [ok] Resume brief: builds ({brief.get('estimated_tokens', 0)} est. tokens)")
        lines.append(f"       Sections: {sections or '(none)'}")
    else:
        skipped = ", ".join(str(s) for s in brief.get("sections_skipped") or [])
        lines.append(f"  [!!] Resume brief: failed ({skipped or 'unknown'})")

    if report.get("cross_tool_ready"):
        lines.append("Result: ready for cross-tool handoff proof.")
    elif session_count:
        lines.append("Result: single-tool evidence only; use another connected tool to complete the proof.")
    else:
        lines.append("Result: no saved sessions yet; run an AI session and wrap up to create evidence.")

    return "\n".join(lines) + "\n"
