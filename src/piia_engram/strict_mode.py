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
    "memory_store",  # every kind, playbooks included, is a pending proposal
    "ingest_notes",
    "extract_session_insights",
    "wrap_up_session",
    "onboard_repo",
    "add_playbook",  # a pending proposal under strict (plan V4 (v))
    "manage_playbook",  # update becomes a proposal; archive/delete/restore refused in the tool
    # no knowledge change
    "review_staging",  # list and dry-run only; enforced inside the tool
    "playbook_execution",
    "save_agent_context",
    "start_project",
    "register_tool",
    "user_portrait",
    # check_anchors is NOT here: a failed anchor demotes verified rows and
    # adopt_legacy writes provenance, both Owner decisions (engram anchors check).
    # exports: files on disk, never knowledge rows
    "export_engram",
    "export_knowledge_report",
    "get_identity_card",
    "refresh_quick_context",
    "request_outline_review",
})

OWNER_CLI_HINT = "engram review apply <marks.json> --operator <name> --yes"


MARKER = "approval_mode.json"
_LATCH_WARNED: set[str] = set()


def _env_strict() -> bool:
    return os.environ.get("ENGRAM_APPROVAL", "").strip().lower() == "strict"


def _store_root(root=None) -> Path:
    """The store the check is about: the caller's root, else the same resolution
    Engram itself uses (ENGRAM_DIR with ~ expanded, else ~/.engram or legacy ~/.piia)."""
    if root:
        return Path(root)
    from .storage import _engram_root

    return _engram_root()


def approval_strict(root=None) -> bool:
    """Effective strict: ENGRAM_APPROVAL=strict, or the store is latched.

    A store that once ran strict carries ``approval_mode.json`` and stays strict
    until the Owner clears it with ``engram review strict-marker --clear``.
    """
    if _env_strict():
        return True
    return (_store_root(root) / MARKER).is_file()


def latch_note(root=None) -> str:
    """Non-empty when the store is latched but ENGRAM_APPROVAL is not strict."""
    if _env_strict() or not (_store_root(root) / MARKER).is_file():
        return ""
    return (
        "strict is latched by approval_mode.json while ENGRAM_APPROVAL is unset; this store "
        "stays strict. To leave strict mode on purpose: engram review strict-marker --clear "
        "--operator <name> --yes"
    )


def bootstrap(root, *, source: str) -> str:
    """Process start (MCP server, CLI apply): latch under strict, or report a latch.

    Writes the marker only when ENGRAM_APPROVAL=strict -- atomically, under the
    store root's write lock, keeping ``strict_first_seen_at``. Tool calls never
    call this. Returns the latch note (empty when there is nothing to report).
    """
    import platform
    from datetime import datetime, timezone

    from .storage import _update_json

    store = _store_root(root)
    if _env_strict():
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _latch(current):
            data = dict(current) if isinstance(current, dict) else {}
            data.setdefault("strict_first_seen_at", now)
            data["strict_last_seen_at"] = now
            data["last_host"] = platform.node()
            data["last_source"] = source
            return data

        store.mkdir(parents=True, exist_ok=True)
        _update_json(store / MARKER, _latch, default={})
        return ""
    note = latch_note(store)
    if note and str(store) not in _LATCH_WARNED:
        _LATCH_WARNED.add(str(store))
        from .audit import AuditLogger, audit_enabled_by_env

        AuditLogger(store / "audit.log", enabled=audit_enabled_by_env()).log(
            "warn", "strict_mode", detail="strict_latched_env_unset", source_tool=source,
        )
    return note


def clear_marker(root) -> bool:
    path = _store_root(root) / MARKER
    if not path.is_file():
        return False
    path.unlink()
    return True


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
    if not approval_strict(root) or tool in STRICT_MCP_ALLOWLIST:
        return None
    return refuse(root, tool=tool)
