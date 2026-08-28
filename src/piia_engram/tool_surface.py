"""Pure MCP capability-group definitions shared by runtime and setup code."""

from __future__ import annotations


TIER1_TOOLS = frozenset({
    # Session lifecycle
    "get_user_context",
    "wrap_up_session",
    # Knowledge write
    "memory_store",
    "add_lesson",
    "add_decision",
    "add_playbook",
    # Knowledge read
    "search_knowledge",
    "get_relevant_knowledge",
    "get_recall",
    "get_knowledge_history",
    # Identity
    "get_identity_card",
    "update_identity",
    # Project context
    "get_project_context",
    "save_project_snapshot",
    # Agent context recovery
    "get_recent_context",
    "get_daily_log",
    "get_resume_brief",
    "get_wrap_up_session_status",
    # Diagnostics
    "doctor",
})

CAPABILITY_GROUPS: dict[str, frozenset[str]] = {
    "knowledge": frozenset({
        "refresh_quick_context", "get_identity_facets", "get_lessons",
        "get_decisions", "list_projects", "get_knowledge_inheritance",
        "get_knowledge_overview", "explore_knowledge", "export_knowledge_report",
        "ingest_notes", "extract_session_insights", "update_knowledge",
        "archive_knowledge", "confirm_knowledge", "onboard_repo", "onboard_accept",
        "check_anchors", "review_staging", "get_stale_knowledge",
        "request_outline_review", "merge_knowledge", "manage_relation",
        "get_playbooks", "manage_playbook", "playbook_execution",
    }),
    "governance": frozenset({
        "get_permission_profile", "manage_caller_trust", "get_audit_log",
        "preview_context_governance",
    }),
    "admin": frozenset({
        "user_portrait", "export_engram", "import_engram",
        "export_feedback_report", "start_project", "save_agent_context",
        "list_agent_sessions",
    }),
    "integrations": frozenset({
        "read_web_content", "register_tool", "find_tool", "list_tools",
    }),
}

ALL_CAPABILITY_TOOLS = frozenset().union(
    TIER1_TOOLS,
    *CAPABILITY_GROUPS.values(),
)
CAPABILITY_MODE_NAMES = frozenset({"core", "all", *CAPABILITY_GROUPS})


def resolve_capability_mode_details(raw: str | None) -> dict[str, object]:
    """Resolve a composable ENGRAM_TOOLS value without importing the MCP server."""
    normalized = "" if raw is None else str(raw).strip()
    if not normalized:
        return {
            "raw": "" if raw is None else str(raw),
            "modes": frozenset({"core"}),
            "unknown_tokens": (),
            "fallback_to_core": False,
            "tools": TIER1_TOOLS,
        }

    tokens = [token.strip().lower() for token in normalized.split("+")]
    tokens = [token for token in tokens if token]
    known: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        if token in CAPABILITY_MODE_NAMES:
            known.append(token)
        else:
            unknown.append(token)

    if "all" in known:
        modes = frozenset({"all"})
        tools = ALL_CAPABILITY_TOOLS
        fallback_to_core = False
    elif known:
        modes = frozenset({"core", *known})
        tools = TIER1_TOOLS
        for mode in modes:
            if mode != "core":
                tools = tools | CAPABILITY_GROUPS[mode]
        fallback_to_core = False
    else:
        modes = frozenset({"core"})
        tools = TIER1_TOOLS
        fallback_to_core = bool(unknown)

    return {
        "raw": str(raw),
        "modes": modes,
        "unknown_tokens": tuple(dict.fromkeys(unknown)),
        "fallback_to_core": fallback_to_core,
        "tools": frozenset(tools),
    }


def capability_mode_tool_count(raw: str | None) -> int:
    """Return the number of tools exposed by one capability-mode expression."""
    return len(resolve_capability_mode_details(raw)["tools"])


def mcp_surface_counts() -> dict[str, int]:
    """Return the canonical total/core/advanced tool split."""
    core = len(TIER1_TOOLS)
    total = len(ALL_CAPABILITY_TOOLS)
    return {
        "total": total,
        "core": core,
        "advanced": total - core,
    }
