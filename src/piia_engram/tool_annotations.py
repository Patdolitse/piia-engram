"""MCP tool annotations: one explicit table, applied after the tools register.

Each tool carries the four standard hints (MCP ``ToolAnnotations``):

``readOnlyHint``     the tool does not change the memory store
``destructiveHint``  the tool's normal function overwrites, downgrades or retires
                     existing records the caller names, as opposed to only
                     adding or stamping them
``idempotentHint``   calling it again with the same arguments changes nothing more
``openWorldHint``    reaching outside the machine is the tool's function (it
                     fetches a URL)

The hints are advice for the client (for example, which calls to confirm). They
are not access control: who may read or write is decided by strict mode and
governance, never by these values.

How the table was drawn up: a first pass from ``TOOL_GOVERNANCE_CLASS``
(``read`` tools read-only; every other class not), then each tool reviewed
against its code and, for the read class, against a before/after check of the
store's files.

* Local usage telemetry and the session checkpoint that every tool call may
  record are not counted as a change to the store.
* Bookkeeping the user never sees is not a write. A read tool that only bumps
  an access counter or logs a usage event stays ``readOnlyHint=True``:
  ``get_lessons``, ``get_decisions``, ``get_playbooks`` and
  ``get_relevant_knowledge`` count a use on the rows they return (for the
  owner) and ``get_user_context`` logs a local usage event. None of them changes
  what a memory says. Marking them as writes would make clients ask for
  confirmation on the most used reads, which helps nobody.
* ``destructiveHint`` errs towards warning once too often rather than missing a
  case. It is True where the tool's normal function overwrites, downgrades or
  retires records the caller names, or edits one in place:
  ``update_knowledge`` changes fields where they stand
  (``update_knowledge`` can also set a status that retires the item);
  ``register_tool`` replaces the fields of a same-named entry and keeps no
  history; ``save_project_snapshot``, ``start_project`` and ``wrap_up_session``
  (which saves a project snapshot) overwrite the snapshot's title, tech stack,
  known issues and notes, and only ``current_state`` keeps up to five earlier
  versions; ``check_anchors`` moves a reviewed item whose anchor no longer holds
  back to pending and clears its confirmation source; ``export_engram``
  overwrites the file at ``output_path`` without asking.
* Not marked destructive: tools that add. The automatic supersede that
  ``add_decision`` and ``memory_store`` can trigger only adds a "supersedes"
  relation; the old item stays and can still be read by id. An item the
  capacity limit moves out of the active files goes to an archive from which it
  can be restored. Stamping provenance, promoting a pending item and
  regenerating a derived export file are not destructive either.
* ``openWorldHint`` is True only for ``read_web_content``, whose function is to
  fetch a URL. The optional anonymous daily usage ping is the product's own
  traffic and is not part of any tool's function.

Tools missing from the table get no annotations (a test keeps the table and the
registered tools in step). An ``mcp`` package without ``ToolAnnotations`` is
detected and skipped silently; one that has it but offers no tool table to
write to is logged as a warning (to the log, never to stdout, which carries the
stdio protocol).
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


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
    "get_decisions": _READ,  # only an access counter on the returned rows (owner): bookkeeping, not a write
    "get_identity_facets": _READ,
    "get_knowledge_history": _READ,
    "get_knowledge_inheritance": _READ,
    "get_knowledge_overview": _READ,
    "get_lessons": _READ,  # only an access counter on the returned rows (owner): bookkeeping, not a write
    "get_permission_profile": _READ,
    "get_playbooks": _READ,  # only an access counter when one playbook is read (owner): bookkeeping
    "get_project_context": _READ,
    "get_recall": _READ,
    "get_recent_context": _READ,
    "get_relevant_knowledge": _READ,  # only an access counter on the returned rows (owner): bookkeeping, not a write
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
    "export_engram": _REMOVES,  # an existing file at output_path is overwritten without a prompt
    "export_knowledge_report": _ADD,  # a new timestamped report file
    "get_identity_card": _STAMP,  # regenerates exports/identity_card.md
    "refresh_quick_context": _STAMP,  # regenerates quick_context.md
    "request_outline_review": _ADD,  # a new review_*.html file
    # --- owner_only_write ---
    "manage_caller_trust": _REMOVES_IDEMPOTENT,  # grant replaces a level; revoke withdraws it
    "import_engram": _READ,  # preview only over MCP; applying is the local `engram import`
    "confirm_knowledge": _STAMP,  # provenance stamp on an existing row
    "onboard_repo": _ADD,  # new pending candidates
    "onboard_accept": _READ,  # local only: over MCP it answers local_review_only and writes nothing
    "check_anchors": _REMOVES_IDEMPOTENT,  # an item whose anchor no longer holds goes back to pending and loses its confirmation source
    # --- governed_write ---
    "memory_store": _ADD,
    "add_lesson": _ADD,
    "add_decision": _ADD,
    "add_playbook": _ADD,
    "ingest_notes": _ADD,
    "extract_session_insights": _ADD,
    "save_agent_context": _ADD,
    "update_knowledge": _REMOVES,  # edits fields in place and can set a retiring status (the old body is kept as a snapshot)
    "archive_knowledge": _REMOVES_IDEMPOTENT,
    "review_staging": _STAMP,  # list / dry-run preview; review_item refreshes last_reviewed; deciding is local only
    "merge_knowledge": _REMOVES,  # archives the secondary item
    "manage_relation": _REMOVES_IDEMPOTENT,  # unlink removes an edge
    "update_identity": _ADD,  # pending identity proposals, never replaces approved fields
    "save_project_snapshot": _REMOVES,  # overwrites title, tech stack, known issues, notes; only current_state keeps up to 5 earlier versions
    "start_project": _REMOVES_IDEMPOTENT,  # may overwrite an existing title with the description
    "user_portrait": _REMOVES,  # save prunes older portrait snapshots
    "register_tool": _REMOVES_IDEMPOTENT,  # a same-named entry has its fields replaced, no history
    "manage_playbook": _REMOVES,  # archive / soft-delete
    "playbook_execution": _ADD,  # plan file and step status
    "wrap_up_session": _REMOVES,  # extraction goes to pending; the project snapshot it saves is overwritten like save_project_snapshot
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
        logger.warning(
            "MCP tool annotations not applied: the installed mcp package has no tool table "
            "this version of Engram knows how to update."
        )
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
