"""Owner pins on lessons, decisions and playbooks.

A pin means "keep this and show it first", not "this is always right":

* Only the Owner sets or clears it, with the local ``engram pin <id>`` /
  ``engram unpin <id>`` commands. ``pinned`` and ``pinned_at`` are system
  fields: a value an MCP payload carries is dropped on insert, and an update
  cannot change them.
* Only a trusted entry (reviewed, active, not superseded) can be pinned. A pin
  counts only while the entry stays trusted (:func:`is_pinned`); when the entry
  leaves the trusted state (the Owner archives it, or approves a revision that
  supersedes it) the pin is removed and the removal is audited.
* Exempt from automatic removal: the lifecycle archive selection, the capacity
  rules (review-queue quota, retired overflow) and imports (a merge never
  overwrites a pinned entry; a replace keeps it).
* Over MCP a pinned entry cannot be edited, archived, merged or deleted
  (``pinned_entry``, nothing written). An agent can still propose a revision:
  ``add_lesson`` / ``add_decision`` / ``add_playbook`` with ``supersedes`` set to
  the pinned id. That proposal always waits for the Owner's review, in every
  approval mode.
* Recall shows trusted pinned entries first within their group (stable);
  search only lets a pin decide between equally relevant results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import recall_policy as _recall_policy
from . import write_provenance as _write_provenance

PIN_FIELDS: tuple[str, ...] = ("pinned", "pinned_at")
PINNABLE_KINDS: tuple[str, ...] = ("lesson", "decision", "playbook")
ERROR_PINNED = "pinned_entry"

# The MCP tool an agent uses to propose a revision of a pinned entry.
PROPOSAL_TOOLS = {"lesson": "add_lesson", "decision": "add_decision", "playbook": "add_playbook"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def has_pin(row: Any) -> bool:
    """The stored flag (whether or not the row is still trusted)."""
    return isinstance(row, Mapping) and row.get("pinned") is True


def is_pinned(row: Any) -> bool:
    """A pin that counts: the flag on a row whose own labels are trusted."""
    return _recall_policy.is_pinned(row)


def strip(row: Any) -> Any:
    """Drop the pin fields from a payload (in place); returns it."""
    if isinstance(row, dict):
        for name in PIN_FIELDS:
            row.pop(name, None)
    return row


def clear_stale(rows: Iterable[Any]) -> list[str]:
    """Remove the pin from every row that carries one but is no longer trusted.

    Mutates the rows in place and returns their ids (for the audit line).
    """
    cleared: list[str] = []
    for row in rows or ():
        if has_pin(row) and not _recall_policy.is_trusted(row):
            strip(row)
            cleared.append(str(row.get("id") or ""))
    return cleared


def mcp_origin() -> bool:
    return _write_provenance.current().get("origin") == _write_provenance.ORIGIN_MCP


def mcp_refuses(row: Any) -> bool:
    """A write that runs on behalf of an MCP caller may not change this row."""
    return is_pinned(row) and mcp_origin()


def refusal(item_id: str, kind: str, row: Mapping[str, Any] | None = None) -> dict:
    """The ``pinned_entry`` error: nothing was written; how to propose instead."""
    version = int((row or {}).get("version") or 1)
    tool = PROPOSAL_TOOLS.get(kind, "add_lesson")
    return {
        "error": ERROR_PINNED,
        "item_id": item_id,
        "kind": kind,
        "changed": False,
        "current_version": version,
        "message": (
            "This entry is pinned by the Owner. Over MCP it cannot be edited, archived, merged or "
            "deleted. Submit a revision proposal instead: it waits for the Owner's review."
        ),
        "proposal": {
            "tool": tool,
            "supersedes": item_id,
            "supersedes_expected_version": version,
        },
    }


# ---------------------------------------------------------------------------
# Owner operations (local CLI)
# ---------------------------------------------------------------------------

_NOT_PINNABLE = {
    _recall_policy.PENDING: "it is waiting for review; approve it first",
    _recall_policy.SUPERSEDED: "it has been superseded by a newer version",
    _recall_policy.ARCHIVED: "it is archived or not active",
    _recall_policy.WITHHELD: "it is withheld",
}


def find(eng, item_id: str, kind: str | None = None) -> tuple[str | None, dict | None]:
    """The lesson, decision or playbook ``item_id`` (restricted to ``kind`` when given)."""
    lessons, decisions, playbooks = eng._read_link_collections()
    collections = {"lesson": lessons, "decision": decisions, "playbook": playbooks}
    kinds = (kind,) if kind else PINNABLE_KINDS
    for name in kinds:
        for row in collections.get(name) or ():
            if str(row.get("id") or "") == item_id:
                return name, row
    return None, None


def _audit(eng, verb: str, kind: str, item_id: str, **extra: Any) -> None:
    eng._audit.log(
        "owner_cli" if verb in ("pin", "unpin") else "write",
        f"pin/{verb}",
        detail=f"{kind} {item_id}",
        source_tool="cli" if verb in ("pin", "unpin") else "",
        extra={"kind": kind, "id": item_id, **extra},
    )


def _write(eng, kind: str, item_id: str, mutate) -> bool:
    """Apply ``mutate`` to the row under its write lock; False when it skipped."""
    from .storage import SkipWrite

    box = {"changed": False}

    def _locked(entry: dict) -> dict:
        if not mutate(entry):
            raise SkipWrite
        box["changed"] = True
        return entry

    eng._update_knowledge_item(kind, item_id, _locked)
    return box["changed"]


def pin(eng, item_id: str, kind: str | None = None) -> dict:
    """Pin a trusted entry. Metadata only: the content and its version stay as they are."""
    found_kind, row = find(eng, item_id, kind)
    if row is None:
        return {"error": "not_found", "id": item_id, "kind": kind or "",
                "message": "No lesson, decision or playbook has this id."}
    verdict = _recall_policy.classify(row, eng._recall_supersede_index())
    if verdict.state != _recall_policy.TRUSTED:
        return {"error": "not_trusted", "id": item_id, "kind": found_kind, "state": verdict.state,
                "message": f"Only trusted entries can be pinned; this one is {verdict.state}: "
                           f"{_NOT_PINNABLE.get(verdict.state, 'it is not trusted')}."}
    if has_pin(row):
        return {"status": "already_pinned", "id": item_id, "kind": found_kind,
                "pinned_at": row.get("pinned_at", "")}
    stamp = _now_iso()

    def _mutate(entry: dict) -> bool:
        if has_pin(entry) or not _recall_policy.is_trusted(entry):
            return False
        entry["pinned"] = True
        entry["pinned_at"] = stamp
        return True

    if not _write(eng, found_kind, item_id, _mutate):
        return {"error": "not_trusted", "id": item_id, "kind": found_kind,
                "message": "The entry changed while pinning; nothing was written."}
    _audit(eng, "pin", found_kind, item_id)
    return {"status": "pinned", "id": item_id, "kind": found_kind, "pinned_at": stamp}


def unpin(eng, item_id: str, kind: str | None = None) -> dict:
    found_kind, row = find(eng, item_id, kind)
    if row is None:
        return {"error": "not_found", "id": item_id, "kind": kind or "",
                "message": "No lesson, decision or playbook has this id."}
    if not has_pin(row):
        return {"status": "not_pinned", "id": item_id, "kind": found_kind}
    if not _write(eng, found_kind, item_id, lambda entry: has_pin(entry) and (strip(entry) is entry)):
        return {"status": "not_pinned", "id": item_id, "kind": found_kind}
    _audit(eng, "unpin", found_kind, item_id)
    return {"status": "unpinned", "id": item_id, "kind": found_kind}


def list_pinned(eng) -> list[dict]:
    """Metadata of every pinned entry: kind, id, pinned_at, version, whether it still counts."""
    lessons, decisions, playbooks = eng._read_link_collections()
    index = eng._recall_supersede_index()
    out: list[dict] = []
    for kind, rows in (("lesson", lessons), ("decision", decisions), ("playbook", playbooks)):
        for row in rows or ():
            if not has_pin(row):
                continue
            state = _recall_policy.classify(row, index).state
            out.append({
                "kind": kind,
                "id": str(row.get("id") or ""),
                "pinned_at": str(row.get("pinned_at") or ""),
                "version": int(row.get("version") or 1),
                "state": state,
            })
    return out


def pinned_ids(eng) -> dict[str, set[str]]:
    """kind -> ids of the entries whose pin counts (trusted)."""
    lessons, decisions, playbooks = eng._read_link_collections()
    out: dict[str, set[str]] = {}
    for kind, rows in (("lesson", lessons), ("decision", decisions), ("playbook", playbooks)):
        out[kind] = {str(r.get("id") or "") for r in rows or () if is_pinned(r)}
    return out


def auto_unpin(eng, item_id: str, *, reason: str, by: str = "") -> bool:
    """Remove the pin of an entry that was just superseded; audited. False when not pinned."""
    kind, row = find(eng, item_id)
    if row is None or not has_pin(row):
        return False
    if not _write(eng, kind, item_id, lambda entry: has_pin(entry) and (strip(entry) is entry)):
        return False
    audit_auto_unpin(eng, kind, [item_id], reason=reason, by=by)
    return True


def audit_auto_unpin(eng, kind: str, ids: Iterable[str], *, reason: str, by: str = "") -> None:
    for item_id in ids:
        if not item_id:
            continue
        extra = {"reason": reason}
        if by:
            extra["by"] = by
        _audit(eng, "auto_unpin", kind, item_id, **extra)
