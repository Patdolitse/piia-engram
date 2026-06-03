"""Owner-confirmed near-duplicate merge *apply* path (N4).

``reports_analytics.suggest_merges`` scores near-duplicate lesson/decision pairs
and proposes a primary/secondary fold, but never mutates. This module is the
separate, explicit, owner-gated step that *acts* on those suggestions: it folds
each secondary item into its primary via the existing reviewed
``Engram.merge_knowledge`` primitive. That primitive is a **reversible soft
archive** - it transfers the secondary's relations to the primary and marks the
secondary ``status="outdated"`` with ``merged_into=<primary>``; it never
hard-deletes and never removes the row.

Safety contract (mirrors the lifecycle apply path):
- **dry-run is the default** and mutates nothing - it returns the plan only;
- **apply without confirm fails closed**: it reports ``requires_confirmation``
  and mutates nothing;
- a **confirmed** apply merges only the proposed/eligible pairs;
- a pair whose secondary is no longer active (e.g. already merged) or whose ids
  are missing / identical is a reported **skip**, never an error/crash;
- the returned payload and audit trail are **metadata only** - ids, entry type,
  similarity score, outcome and a transferred-relation count, never stored
  bodies or summaries.

This is intentionally a CLI / owner-only surface; no agent-facing MCP apply tool
is exposed in this slice (no new agent-facing mutation surface).
"""

from __future__ import annotations

from typing import Any, Iterable

APPLY_ACTION = "near_duplicate_merge_apply"

# Per-pair outcome codes (metadata only).
OUTCOME_PLANNED = "planned"
OUTCOME_PENDING = "pending_confirmation"
OUTCOME_MERGED = "merged"
OUTCOME_SKIPPED = "skipped"


def _normalize_pairs(
    pairs: Iterable[Any],
) -> list[tuple[str, str, str, float]]:
    """Coerce mixed pair inputs to ``(primary_id, secondary_id, type, sim)``.

    Accepts ``(primary, secondary)`` tuples/lists or ``suggest_merges`` dicts
    (``{primary_id, secondary_id, type, similarity}``). Malformed entries are
    dropped rather than raising - the apply path never crashes on bad input.
    """
    out: list[tuple[str, str, str, float]] = []
    for pair in pairs or []:
        primary = secondary = ""
        entry_type = "unknown"
        sim = 0.0
        if isinstance(pair, dict):
            primary = str(pair.get("primary_id") or "")
            secondary = str(pair.get("secondary_id") or "")
            entry_type = str(pair.get("type") or pair.get("entry_type") or "unknown")
            try:
                sim = float(pair.get("similarity", 0.0))
            except (TypeError, ValueError):
                sim = 0.0
        elif isinstance(pair, (tuple, list)) and len(pair) >= 2:
            primary = str(pair[0] or "")
            secondary = str(pair[1] or "")
        else:
            continue
        out.append((primary, secondary, entry_type, sim))
    return out


def apply_merge(
    eng,
    *,
    pairs: Iterable[Any] | None = None,
    threshold: float = 0.45,
    limit: int = 10,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply (or preview) near-duplicate merges over active knowledge.

    Args:
        pairs: explicit ``(primary, secondary)`` pairs or ``suggest_merges``
            suggestion dicts. ``None`` derives candidates from
            ``eng.suggest_merges(threshold, limit)``.
        threshold/limit: forwarded to ``suggest_merges`` when ``pairs`` is None.
        confirm: required (with ``dry_run=False``) to actually mutate.
        dry_run: when True (default) nothing is mutated - the plan is returned.

    Returns a metadata-only payload (see module docstring for the contract).
    """
    if pairs is None:
        suggestions = []
        if hasattr(eng, "suggest_merges"):
            try:
                suggestions = eng.suggest_merges(
                    threshold=threshold, limit=limit
                ).get("suggestions", [])
            except Exception:  # pragma: no cover - defensive
                suggestions = []
        normalized = _normalize_pairs(suggestions)
    else:
        normalized = _normalize_pairs(pairs)

    items: list[dict[str, Any]] = []
    counts = {"planned": 0, "merged": 0, "skipped": 0}

    # 1) Classify every pair first (pure), so dry-run / fail-closed can report
    #    without touching storage. A pair is eligible only if both ids resolve
    #    to *active* items and are distinct.
    for primary, secondary, entry_type, sim in normalized:
        reason = ""
        eligible = True
        if not primary or not secondary:
            eligible, reason = False, "missing_id"
        elif primary == secondary:
            eligible, reason = False, "self_merge"
        else:
            p_type, p_item = _lookup(eng, primary)
            s_type, s_item = _lookup(eng, secondary)
            if p_item is None:
                eligible, reason = False, "primary_not_found"
            elif s_item is None:
                eligible, reason = False, "secondary_not_found"
            elif p_item.get("status") != "active":
                eligible, reason = False, "primary_not_active"
            elif s_item.get("status") != "active":
                eligible, reason = False, "secondary_not_active"
            elif entry_type == "unknown" and s_type:
                entry_type = s_type

        item = {
            "primary_id": primary,
            "secondary_id": secondary,
            "entry_type": entry_type,
            "similarity": round(float(sim), 4),
            "outcome": OUTCOME_PLANNED if eligible else OUTCOME_SKIPPED,
            "reason": reason,
            "related_ids_transferred": 0,
        }
        if eligible:
            counts["planned"] += 1
        else:
            counts["skipped"] += 1
        items.append(item)

    eligible_items = [it for it in items if it["outcome"] == OUTCOME_PLANNED]

    # 2) Dry-run (default): preview only, mutate nothing.
    if dry_run:
        return _payload(
            dry_run=True, confirmed=bool(confirm), requires_confirmation=False,
            changed=False, status="dry_run", counts=counts, items=items,
        )

    # 3) Apply without confirm: fail closed.
    if not confirm:
        for it in eligible_items:
            it["outcome"] = OUTCOME_PENDING
        return _payload(
            dry_run=False, confirmed=False, requires_confirmation=True,
            changed=False, status="confirmation_required", counts=counts,
            items=items,
        )

    # 4) Confirmed apply: merge each eligible pair via the soft-archive primitive.
    changed_any = False
    for it in eligible_items:
        result = eng.merge_knowledge(it["primary_id"], it["secondary_id"])
        if result.get("success"):
            it["outcome"] = OUTCOME_MERGED
            it["related_ids_transferred"] = int(
                result.get("related_ids_transferred", 0) or 0
            )
            counts["planned"] -= 1
            counts["merged"] += 1
            changed_any = True
        else:
            # State changed under us (e.g. secondary became inactive) - skip.
            it["outcome"] = OUTCOME_SKIPPED
            it["reason"] = "merge_refused"
            counts["planned"] -= 1
            counts["skipped"] += 1

    return _payload(
        dry_run=False, confirmed=True, requires_confirmation=False,
        changed=changed_any, status="applied", counts=counts, items=items,
    )


def _lookup(eng, item_id: str):
    """Best-effort, access-neutral lookup of one item; (None, None) on any issue."""
    finder = getattr(eng, "_find_item_by_id", None)
    if not callable(finder):
        return None, None
    try:
        return finder(item_id)
    except Exception:  # pragma: no cover - defensive
        return None, None


def _payload(
    *,
    dry_run: bool,
    confirmed: bool,
    requires_confirmation: bool,
    changed: bool,
    status: str,
    counts: dict[str, int],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": 1,
        "action": APPLY_ACTION,
        "dry_run": bool(dry_run),
        "confirmed": bool(confirmed),
        "requires_confirmation": bool(requires_confirmation),
        "changed": bool(changed),
        "status": status,
        "counts": counts,
        "items": items,
    }


def render_merge_apply_text(payload: dict[str, Any]) -> str:
    """Render the merge apply payload as an owner-facing, metadata-only digest."""
    counts = payload.get("counts", {})
    mode = "dry-run" if payload.get("dry_run") else "apply"
    lines = [
        f"Near-duplicate merge {mode} - status: {payload.get('status')}",
        f"  planned: {counts.get('planned', 0)}  "
        f"merged: {counts.get('merged', 0)}  "
        f"skipped: {counts.get('skipped', 0)}",
    ]
    if payload.get("requires_confirmation"):
        lines.append("  confirmation required - re-run with --commit --yes to apply.")
    flagged = [it for it in payload.get("items", [])
               if it.get("outcome") in (OUTCOME_PLANNED, OUTCOME_PENDING, OUTCOME_MERGED)]
    for it in flagged[:50]:
        lines.append(
            f"    - [{it.get('outcome')}] {it.get('primary_id')} <- "
            f"{it.get('secondary_id')} type={it.get('entry_type')} "
            f"sim={it.get('similarity')}"
        )
    lines.append(
        "  reversible soft archive only - the secondary is marked outdated "
        "(merged_into), never hard-deleted."
    )
    return "\n".join(lines)
