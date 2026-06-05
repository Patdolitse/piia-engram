"""Metadata-only batch review helpers for staging knowledge."""

from __future__ import annotations

from typing import Any


VALID_ACTIONS = {"approve", "reject", "archive"}


def batch_review_staging(
    eng,
    actions: list[dict[str, Any]] | None,
    *,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview or apply staging approve/reject actions.

    ``dry_run`` is the default. Mutations require ``dry_run=False`` and
    ``confirm=True``. Returned payload is metadata-only: ids, action labels,
    status codes and counts, never stored bodies.
    """
    rows = [a for a in (actions or []) if isinstance(a, dict)]
    counts = {
        "requested": len(rows),
        "approve": 0,
        "reject": 0,
        "planned": 0,
        "applied": 0,
        "noop": 0,
        "failed": 0,
    }
    items: list[dict[str, Any]] = []

    for idx, row in enumerate(rows):
        item_id = str(row.get("id") or row.get("item_id") or "").strip()
        action = str(row.get("action") or "").strip().lower()
        if action == "archive":
            action = "reject"
        if action not in {"approve", "reject"}:
            items.append(_item(idx, item_id, action or "unknown", "invalid_action"))
            counts["failed"] += 1
            continue
        counts[action] += 1
        if not item_id:
            items.append(_item(idx, item_id, action, "missing_id"))
            counts["failed"] += 1
            continue

        item_type, item = eng._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision"}:
            items.append(_item(idx, item_id, action, "not_found"))
            counts["failed"] += 1
            continue
        if item.get("tier") != "staging":
            items.append(_item(idx, item_id, action, "not_staging", item_type=item_type))
            counts["noop"] += 1
            continue

        items.append(_item(idx, item_id, action, "planned", item_type=item_type))
        counts["planned"] += 1

    if dry_run:
        return _payload(
            status="dry_run",
            dry_run=True,
            confirmed=bool(confirm),
            requires_confirmation=False,
            changed=False,
            counts=counts,
            items=items,
        )

    planned = [it for it in items if it["status"] == "planned"]
    if planned and not confirm:
        for it in planned:
            it["status"] = "pending_confirmation"
        return _payload(
            status="confirmation_required",
            dry_run=False,
            confirmed=False,
            requires_confirmation=True,
            changed=False,
            counts=counts,
            items=items,
        )

    changed = False
    for it in planned:
        if it["action"] == "approve":
            result = eng.promote_knowledge(it["id"])
            ok = result.get("status") == "promoted"
        else:
            result = eng.archive_knowledge(it["id"])
            ok = not result.get("error")
        if ok:
            it["status"] = "applied"
            counts["applied"] += 1
            changed = True
        else:
            it["status"] = "failed"
            counts["failed"] += 1

    return _payload(
        status="applied",
        dry_run=False,
        confirmed=True,
        requires_confirmation=False,
        changed=changed,
        counts=counts,
        items=items,
    )


def _item(
    index: int,
    item_id: str,
    action: str,
    status: str,
    *,
    item_type: str = "",
) -> dict[str, Any]:
    return {
        "candidate_ref": index,
        "id": item_id,
        "action": action,
        "status": status,
        "type": item_type,
    }


def _payload(
    *,
    status: str,
    dry_run: bool,
    confirmed: bool,
    requires_confirmation: bool,
    changed: bool,
    counts: dict[str, int],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": 1,
        "action": "staging_batch_review",
        "status": status,
        "dry_run": bool(dry_run),
        "confirmed": bool(confirmed),
        "requires_confirmation": bool(requires_confirmation),
        "changed": bool(changed),
        "counts": counts,
        "items": items,
    }
