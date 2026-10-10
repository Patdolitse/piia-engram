"""Deciding a pending proposal is the Owner's local review, in every approval mode.

An AI proposes over MCP; it never approves, promotes, rejects or archives a
pending (staging) row, its own or another's. The shared core operations
(promotion, playbook approval/rejection, archive, tier/status edits, review
application) call these helpers, so every MCP entry point gets the same rule;
the Owner decides with the local ``engram review``. Writes that the risk gate
admits directly as verified (admission, not approval) are not affected.
"""

from __future__ import annotations

from typing import Any

from . import write_provenance as _write_provenance

LOCAL_REVIEW_ONLY = "local_review_only"
HINT = "open `piia-engram-review` locally, or run `engram review`"


def mcp_origin() -> bool:
    return _write_provenance.current().get("origin") == _write_provenance.ORIGIN_MCP


def is_pending(row: Any) -> bool:
    return isinstance(row, dict) and str(row.get("tier") or "") == "staging"


def refusal(item_id: str = "", *, action: str = "", hint: str = HINT) -> dict:
    """The ``local_review_only`` reply: nothing was written."""
    out = {
        "error": LOCAL_REVIEW_ONLY,
        "status": LOCAL_REVIEW_ONLY,
        "changed": False,
        "hint": hint,
        "message": "Approving, promoting, rejecting or archiving a pending proposal is the Owner's "
                   "decision in the local engram review; nothing was written.",
    }
    if item_id:
        out["item_id"] = item_id
    if action:
        out["action"] = action
    return out


def refuses_decision(row: Any) -> bool:
    """An MCP caller may not decide (retire, approve) this pending row."""
    return mcp_origin() and is_pending(row)


def update_refusal(row: Any, updates: Any, item_id: str = "") -> dict | None:
    """The refusal for an MCP edit that would decide a proposal, else None.

    Refused over MCP: any tier or status change of a pending row, and raising
    any row to the verified tier. Content edits of a pending row (the proposal
    itself) and edits of trusted rows are left to the other guards.
    """
    if not mcp_origin() or not isinstance(row, dict) or not isinstance(updates, dict):
        return None
    rid = item_id or str(row.get("id") or "")
    if "tier" in updates and updates["tier"] != row.get("tier"):
        if is_pending(row) or str(updates["tier"]) == "verified":
            return refusal(rid, action="tier")
    if is_pending(row) and "status" in updates and updates["status"] != row.get("status", "active"):
        return refusal(rid, action="status")
    return None
