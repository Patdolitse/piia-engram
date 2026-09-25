"""Strict approval mode (``ENGRAM_APPROVAL=strict``) at the MCP boundary.

Under strict, an agent may only *propose* over MCP: lesson/decision rows land in
staging and wait for the Owner. Every other mutating MCP tool is refused by
default -- identity edits, content/tier edits, approvals, merges, imports,
relation edits -- and the Owner applies decisions with the local
``engram review`` CLI instead. The refusal is independent of the caller's
self-reported trust, because an agent can claim any client type.

With the variable unset, nothing here changes behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

# Mutating MCP tools that stay callable under strict. Anything else in a mutating
# governance class is refused (default-refuse).
STRICT_MCP_ALLOWLIST = frozenset({
    # proposals: the knowledge rows they create are forced into staging
    "add_lesson",
    "add_decision",
    "memory_store",  # kind=playbook is refused inside the tool
    "ingest_notes",
    "extract_session_insights",
    "wrap_up_session",
    "onboard_repo",
    # no knowledge change
    "review_staging",  # list and dry-run only; enforced inside the tool
    "playbook_execution",
    "save_agent_context",
    "start_project",
    "register_tool",
    "check_anchors",
    "user_portrait",
    # exports: files on disk, never knowledge rows
    "export_engram",
    "export_knowledge_report",
    "get_identity_card",
    "refresh_quick_context",
    "request_outline_review",
})

OWNER_CLI_HINT = "engram review apply <marks.json> --operator <name> --yes"


def approval_strict() -> bool:
    return os.environ.get("ENGRAM_APPROVAL", "").strip().lower() == "strict"


def refuse(root, *, tool: str, detail: str = "") -> str:
    """Record the refusal in audit.log and return the governance refusal string."""
    from . import governance_runtime as _gov_rt
    from .audit import AuditLogger, audit_enabled_by_env

    AuditLogger(Path(root) / "audit.log", enabled=audit_enabled_by_env()).log(
        "refused",
        f"mcp/{tool}",
        detail=f"strict_owner_only{': ' + detail if detail else ''}",
        source_tool=_gov_rt.current_client_type(),
    )
    return _gov_rt._refusal(
        f"ENGRAM_APPROVAL=strict: {tool}{' ' + detail if detail else ''} is an Owner action and "
        "is refused over MCP. Agents propose with add_lesson / add_decision / memory_store; "
        f"the Owner decides locally: {OWNER_CLI_HINT}"
    )


def maybe_refuse(root, *, tool: str) -> str | None:
    """Default-refuse gate for mutating MCP tools under strict."""
    if not approval_strict() or tool in STRICT_MCP_ALLOWLIST:
        return None
    return refuse(root, tool=tool)
