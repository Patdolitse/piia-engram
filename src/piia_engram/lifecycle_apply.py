"""Owner-confirmed lifecycle archive *apply* path (N1).

``lifecycle.py`` scores entries and proposes archive/prune candidates but never
mutates. This module is the separate, explicit, owner-gated step that *acts* on
those proposals: it moves selected eligible entries into the ``archived`` tier
via a reversible soft archive (``Engram.soft_archive_knowledge_tier``). It never
hard-deletes and never touches verified/trusted knowledge.

Safety contract (enforced here and re-checked in core):
- **dry-run is the default** and mutates nothing - it returns the plan only;
- **apply without confirm fails closed**: it reports ``requires_confirmation``
  and mutates nothing;
- a **confirmed** apply archives only proposed *eligible* ids (archive/prune
  candidates that are not verified);
- already-archived ids are idempotent no-ops (``changed`` False);
- the returned payload and the audit trail are **metadata only** - ids, types,
  decay scores, reason codes, prior tier and timestamps, never stored bodies or
  private project paths.

This is intentionally a CLI / owner-only surface; no agent-facing MCP apply tool
is exposed in this slice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import lifecycle as _lifecycle

APPLY_ACTION = "lifecycle_archive_apply"

# Per-item outcome codes (metadata only).
OUTCOME_PLANNED = "planned"
OUTCOME_PENDING = "pending_confirmation"
OUTCOME_ARCHIVED = "archived"
OUTCOME_ALREADY = "already_archived"
OUTCOME_PROTECTED = "protected"
OUTCOME_INELIGIBLE = "ineligible"
OUTCOME_NOT_FOUND = "not_found"


def _item_metadata(proposal: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Project a scored proposal down to a metadata-only apply item."""
    return {
        "id": proposal.get("id", ""),
        "entry_type": proposal.get("entry_type", "unknown"),
        "prior_tier": proposal.get("tier", ""),
        "decay_score": proposal.get("decay_score", 0.0),
        "proposal": proposal.get("proposal", ""),
        "reasons": list(proposal.get("reasons", [])),
        "outcome": outcome,
        "archived_at": None,
    }


def apply_lifecycle_archive(
    eng,
    *,
    ids: list[str] | None = None,
    confirm: bool = False,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply (or preview) the lifecycle archive over active lessons + decisions.

    Args:
        ids: explicit subset of entry ids to act on; ``None`` means all eligible.
        confirm: required (with ``dry_run=False``) to actually mutate.
        dry_run: when True (default) nothing is mutated - the plan is returned.
        now: optional fixed clock for deterministic scoring.

    Returns a metadata-only payload (see module docstring for the contract).
    """
    lessons = eng.get_lessons(limit=None, _update_access=False) or []
    decisions = eng.get_decisions(limit=None, _update_access=False) or []
    report = _lifecycle.build_lifecycle_proposal(
        list(lessons) + list(decisions), now=now
    )
    proposal_by_id = {p.get("id"): p for p in report.get("proposals", [])}
    eligible_ids = set(_lifecycle.select_archive_candidate_ids(report))

    if ids is None:
        # All eligible, in most-decayed-first order.
        targets = [p.get("id") for p in report.get("proposals", [])
                   if p.get("id") in eligible_ids]
    else:
        targets = [str(i) for i in ids]

    items: list[dict[str, Any]] = []
    counts = {
        "eligible": 0, "archived": 0, "already_archived": 0,
        "protected": 0, "ineligible": 0, "not_found": 0,
    }

    # Decide the disposition of each target first (pure classification), so a
    # dry-run / fail-closed path can report it without touching storage.
    for item_id in targets:
        proposal = proposal_by_id.get(item_id)
        if proposal is None:
            item = {
                "id": item_id, "entry_type": "unknown", "prior_tier": "",
                "decay_score": 0.0, "proposal": "", "reasons": [],
                "outcome": OUTCOME_NOT_FOUND, "archived_at": None,
            }
            counts["not_found"] += 1
            items.append(item)
            continue
        if item_id not in eligible_ids:
            if proposal.get("tier") == "verified":
                outcome = OUTCOME_PROTECTED
                counts["protected"] += 1
            elif proposal.get("tier") == "archived":
                outcome = OUTCOME_ALREADY
                counts["already_archived"] += 1
            else:
                outcome = OUTCOME_INELIGIBLE
                counts["ineligible"] += 1
            items.append(_item_metadata(proposal, outcome))
            continue
        counts["eligible"] += 1
        items.append(_item_metadata(proposal, OUTCOME_PLANNED))

    eligible_items = [it for it in items if it["outcome"] == OUTCOME_PLANNED]

    # 1) Dry-run (default): preview only, mutate nothing.
    if dry_run:
        return _payload(
            dry_run=True, confirmed=bool(confirm), requires_confirmation=False,
            changed=False, status="dry_run", counts=counts, items=items,
        )

    # 2) Apply without confirm: fail closed.
    if not confirm:
        for it in eligible_items:
            it["outcome"] = OUTCOME_PENDING
        return _payload(
            dry_run=False, confirmed=False, requires_confirmation=True,
            changed=False, status="confirmation_required", counts=counts,
            items=items,
        )

    # 3) Confirmed apply: archive each eligible target (idempotent + protected).
    changed_any = False
    for it in eligible_items:
        result = eng.soft_archive_knowledge_tier(it["id"], now=_iso(now))
        if result.get("error") == "protected_verified":
            it["outcome"] = OUTCOME_PROTECTED
            counts["eligible"] -= 1
            counts["protected"] += 1
        elif result.get("changed"):
            it["outcome"] = OUTCOME_ARCHIVED
            it["archived_at"] = result.get("archived_at")
            counts["eligible"] -= 1
            counts["archived"] += 1
            changed_any = True
        else:
            # Already archived (or otherwise unchanged) - idempotent no-op.
            it["outcome"] = OUTCOME_ALREADY
            it["archived_at"] = result.get("archived_at")
            counts["already_archived"] += 1

    return _payload(
        dry_run=False, confirmed=True, requires_confirmation=False,
        changed=changed_any, status="applied", counts=counts, items=items,
    )


def _iso(now: datetime | None) -> str | None:
    return now.isoformat() if isinstance(now, datetime) else None


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


def render_lifecycle_apply_text(payload: dict[str, Any]) -> str:
    """Render the apply payload as an owner-facing, metadata-only digest."""
    counts = payload.get("counts", {})
    mode = "dry-run" if payload.get("dry_run") else "apply"
    lines = [
        f"Lifecycle archive {mode} - status: {payload.get('status')}",
        f"  eligible: {counts.get('eligible', 0)}  "
        f"archived: {counts.get('archived', 0)}  "
        f"already_archived: {counts.get('already_archived', 0)}  "
        f"protected: {counts.get('protected', 0)}  "
        f"ineligible: {counts.get('ineligible', 0)}  "
        f"not_found: {counts.get('not_found', 0)}",
    ]
    if payload.get("requires_confirmation"):
        lines.append("  confirmation required - re-run with --commit --yes to apply.")
    flagged = [it for it in payload.get("items", [])
               if it.get("outcome") in (OUTCOME_PLANNED, OUTCOME_PENDING, OUTCOME_ARCHIVED)]
    for it in flagged[:50]:
        label = it.get("id") or "(no id)"
        lines.append(
            f"    - [{it.get('outcome')}] {label} type={it.get('entry_type')} "
            f"prior_tier={it.get('prior_tier') or 'none'} "
            f"score={it.get('decay_score')} reasons={','.join(it.get('reasons', []))}"
        )
    lines.append("  reversible soft archive only - never hard-deletes; restore via 'engram lifecycle restore <id>'.")
    return "\n".join(lines)
