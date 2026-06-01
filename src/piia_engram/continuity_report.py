"""Metadata-only continuity proof for cross-tool handoff."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


_CONTEXT_LOAD_TOOLS = {"get_user_context", "get_resume_brief"}
_WRAP_UP_TOOLS = {"wrap_up_session"}
READINESS_LEVELS = frozenset(
    {
        "not_ready",
        "single_tool_ready",
        "cross_tool_ready",
        "observed_signals",
    }
)


def _nonnegative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _count_selected_tool_calls(tool_calls: Any, names: set[str]) -> int:
    if not isinstance(tool_calls, dict):
        return 0
    total = 0
    for name in names:
        counts = tool_calls.get(name)
        if isinstance(counts, dict):
            total += _nonnegative_int(counts.get("success"))
            total += _nonnegative_int(counts.get("error"))
        else:
            total += _nonnegative_int(counts)
    return total


def _iter_jsonl_dicts(path: Path):
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def _build_recall_signals(root: Path) -> dict[str, Any]:
    telemetry_log = root / "telemetry.log"
    telemetry_present = telemetry_log.is_file()
    telemetry_context_load_calls = 0
    telemetry_wrap_up_calls = 0
    for entry in _iter_jsonl_dicts(telemetry_log):
        tool_calls = entry.get("tool_calls")
        telemetry_context_load_calls += _count_selected_tool_calls(
            tool_calls, _CONTEXT_LOAD_TOOLS
        )
        telemetry_wrap_up_calls += _count_selected_tool_calls(
            tool_calls, _WRAP_UP_TOOLS
        )

    beta_events = root / "beta_events.jsonl"
    beta_present = beta_events.is_file()
    cold_start_events = 0
    session_end_events = 0
    for entry in _iter_jsonl_dicts(beta_events):
        event = entry.get("event")
        if event == "cold_start":
            cold_start_events += 1
        elif event == "session_end":
            session_end_events += 1

    observed = any(
        (
            telemetry_context_load_calls,
            telemetry_wrap_up_calls,
            cold_start_events,
            session_end_events,
        )
    )
    return {
        "observed": observed,
        "context_load_calls": telemetry_context_load_calls,
        "wrap_up_calls": telemetry_wrap_up_calls,
        "telemetry": {
            "present": telemetry_present,
            "context_load_calls": telemetry_context_load_calls,
            "wrap_up_calls": telemetry_wrap_up_calls,
        },
        "beta_events": {
            "present": beta_present,
            "cold_start_events": cold_start_events,
            "session_end_events": session_end_events,
        },
    }


def _readiness_level(checks: dict[str, bool]) -> str:
    """Map metadata checks to a conservative continuity readiness label."""
    if not checks.get("has_saved_sessions"):
        return "not_ready"
    if (
        checks.get("has_multiple_tools")
        and checks.get("resume_brief_builds")
        and checks.get("has_context_load_signal")
        and checks.get("has_wrap_up_signal")
    ):
        return "observed_signals"
    if checks.get("has_multiple_tools") and checks.get("resume_brief_builds"):
        return "cross_tool_ready"
    return "single_tool_ready"


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
    recall_signals = _build_recall_signals(Path(eng.root))
    beta_events = recall_signals.get("beta_events") or {}
    readiness_checks = {
        "has_saved_sessions": session_count > 0,
        "has_multiple_tools": tool_count >= 2,
        "resume_brief_builds": bool(brief_meta["builds"]),
        "has_context_load_signal": _nonnegative_int(
            recall_signals.get("context_load_calls")
        )
        > 0
        or _nonnegative_int(beta_events.get("cold_start_events")) > 0,
        "has_wrap_up_signal": _nonnegative_int(recall_signals.get("wrap_up_calls")) > 0
        or _nonnegative_int(beta_events.get("session_end_events")) > 0,
    }
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
        "recall_signals": recall_signals,
        "readiness_checks": readiness_checks,
        "readiness_level": _readiness_level(readiness_checks),
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

    recall = report.get("recall_signals") or {}
    recall_mark = "ok" if recall.get("observed") else "--"
    beta = recall.get("beta_events") or {}
    lines.append(
        f"  [{recall_mark}] Context load signals: "
        f"{recall.get('context_load_calls', 0)} tool call(s), "
        f"{beta.get('cold_start_events', 0)} cold-start event(s)"
    )
    lines.append(
        f"       Wrap-up signals: {recall.get('wrap_up_calls', 0)} tool call(s), "
        f"{beta.get('session_end_events', 0)} session-end event(s)"
    )

    if report.get("cross_tool_ready"):
        lines.append("Result: ready for cross-tool handoff proof.")
    elif session_count:
        lines.append("Result: single-tool evidence only; use another connected tool to complete the proof.")
    else:
        lines.append("Result: no saved sessions yet; run an AI session and wrap up to create evidence.")

    return "\n".join(lines) + "\n"
