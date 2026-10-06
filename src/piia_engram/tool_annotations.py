"""MCP tool annotations: one explicit table, applied after the tools register.

Each tool carries the four standard hints (MCP ``ToolAnnotations``):

``readOnlyHint``     the tool does not change the memory store
``destructiveHint``  the tool may remove, retire (archive) or replace existing
                     records, as opposed to only adding or stamping them
``idempotentHint``   calling it again with the same arguments changes nothing more
``openWorldHint``    the tool talks to something outside the local machine

The hints are advice for the client (for example, which calls to confirm). They
are not access control: who may read or write is decided by strict mode and
governance, never by these values.

How the table was drawn up: a first pass from ``TOOL_GOVERNANCE_CLASS``
(``read`` tools read-only; every other class not), then each tool reviewed
against its code and, for the read class, against a before/after check of the
store's files.

* Local usage telemetry and the session checkpoint that every tool call may
  record are not counted as a change to the store.
* A read tool that still updates the store is marked ``readOnlyHint=False`` and
  not idempotent, with the reason noted on its row: the four listed below bump
  an access counter on the rows they return (for the owner).
* ``destructiveHint`` is True only where the tool's job includes removing,
  archiving or replacing existing records. Adding rows, stamping provenance,
  promoting a pending row and regenerating a derived export file are not.
* ``openWorldHint`` is True only for ``read_web_content``.

Tools missing from the table get no annotations (a test keeps the table and the
registered tools in step). An ``mcp`` package without ``ToolAnnotations`` is
detected and skipped silently.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class ToolHints(NamedTuple):
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


_READ = ToolHints(read_only=True, destructive=False, idempotent=True)
# read tools that bump an access counter on the rows they return (owner only)
_READ_COUNTS_ACCESS = ToolHints(read_only=False, destructive=False, idempotent=False)
_ADD = ToolHints(read_only=False, destructive=False, idempotent=False)
_STAMP = ToolHints(read_only=False, destructive=False, idempotent=True)
_REMOVES = ToolHints(read_only=False, destructive=True, idempotent=False)
_REMOVES_IDEMPOTENT = ToolHints(read_only=False, destructive=True, idempotent=True)


TOOL_ANNOTATIONS: dict[str, ToolHints] = {
    # --- read: nothing in the store changes ---
    "doctor": _READ,
    "explore_knowledge": _READ,
    "export_feedback_report": _READ,  # counts and distributions only
    "find_tool": _READ,
    "get_audit_log": _READ,
    "get_daily_log": _READ,
    "get_decisions": _READ_COUNTS_ACCESS,  # access counter on returned rows (owner)
    "get_identity_facets": _READ,
    "get_knowledge_history": _READ,
    "get_knowledge_inheritance": _READ,
    "get_knowledge_overview": _READ,
    "get_lessons": _READ_COUNTS_ACCESS,  # access counter on returned rows (owner)
    "get_permission_profile": _READ,
    "get_playbooks": _READ_COUNTS_ACCESS,  # reading one playbook counts as a use (owner)
    "get_project_context": _READ,
    "get_recall": _READ,
    "get_recent_context": _READ,
    "get_relevant_knowledge": _READ_COUNTS_ACCESS,  # access counter on returned rows (owner)
    "get_resume_brief": _READ,
    "get_stale_knowledge": _READ,
    "get_user_context": _READ,  # surfacing is not counted as use; only a local usage event is logged
    "get_wrap_up_session_status": _READ,
    "list_agent_sessions": _READ,
    "list_projects": _READ,
    "list_tools": _READ,
    "preview_context_governance": _READ,
    "read_web_content": ToolHints(True, False, True, open_world=True),  # fetches a URL
    "search_knowledge": _READ,
    # --- export_owner_only: write a file, never touch memory rows ---
    "export_engram": _ADD,  # a backup file at the given path or a dated default
    "export_knowledge_report": _ADD,  # a new timestamped report file
    "get_identity_card": _STAMP,  # regenerates exports/identity_card.md
    "refresh_quick_context": _STAMP,  # regenerates quick_context.md
    "request_outline_review": _ADD,  # a new review_*.html file
    # --- owner_only_write ---
    "manage_caller_trust": _REMOVES_IDEMPOTENT,  # grant replaces a level; revoke withdraws it
    "import_engram": _REMOVES,  # merge=False overwrites the existing store
    "confirm_knowledge": _STAMP,  # provenance stamp on an existing row
    "onboard_repo": _ADD,  # new pending candidates
    "onboard_accept": _STAMP,  # promotes one pending candidate
    "check_anchors": _STAMP,  # writes anchor status and check time
    # --- governed_write ---
    "memory_store": _ADD,
    "add_lesson": _ADD,
    "add_decision": _ADD,
    "add_playbook": _ADD,
    "ingest_notes": _ADD,
    "extract_session_insights": _ADD,
    "save_agent_context": _ADD,
    "update_knowledge": _ADD,  # edits keep the old body as a version snapshot
    "archive_knowledge": _REMOVES_IDEMPOTENT,
    "review_staging": _REMOVES,  # batch reject / apply_text archive
    "merge_knowledge": _REMOVES,  # archives the secondary item
    "manage_relation": _REMOVES_IDEMPOTENT,  # unlink removes an edge
    "update_identity": _REMOVES_IDEMPOTENT,  # replaces field values in place
    "save_project_snapshot": _ADD,  # merges; the previous state moves to history
    "start_project": _ADD,
    "user_portrait": _REMOVES,  # save prunes older portrait snapshots
    "register_tool": _STAMP,  # same name updates the entry
    "manage_playbook": _REMOVES,  # archive / soft-delete
    "playbook_execution": _ADD,  # plan file and step status
    "wrap_up_session": _ADD,  # extraction (pending) and a project snapshot merge
}


def apply_tool_annotations(server: Any) -> int:
    """Set the annotations of every registered tool that has a row. Returns how many.

    Works on an already registered FastMCP server, so the ``@mcp.tool()``
    decorators stay as they are. Does nothing, and never raises, when the
    installed ``mcp`` has no ``ToolAnnotations`` or its tool model has no
    ``annotations`` field.
    """
    try:
        from mcp.types import ToolAnnotations
    except Exception:  # older mcp without annotations
        return 0
    tools = getattr(getattr(server, "_tool_manager", None), "_tools", None)
    if not isinstance(tools, dict):
        return 0
    applied = 0
    for name, tool in list(tools.items()):
        hints = TOOL_ANNOTATIONS.get(name)
        if hints is None:
            continue
        fields = getattr(type(tool), "model_fields", None)
        if fields is not None and "annotations" not in fields:
            continue
        try:
            tool.annotations = ToolAnnotations(**hints.as_dict())
        except Exception:
            continue
        applied += 1
    return applied
