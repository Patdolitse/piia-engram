"""Owner-confirmed reconcile *apply* path - import-only (N2).

``reconcile_proposal.build_reconcile_proposal`` classifies external AI-tool
candidates against the existing store as ``import`` / ``duplicate`` / ``conflict``
/ ``skip``, metadata-only, writing nothing. This module is the separate,
explicit, owner-gated step that acts on that proposal - and **only on the
``import`` verdicts**.

Scope (per the approved slice):
- **import-only**: a candidate classified ``import`` is added via the existing
  ``Engram.add_lesson`` / ``Engram.add_decision`` write methods (tier=staging).
- ``duplicate`` / ``conflict`` / ``skip`` are surfaced as metadata-only no-ops
  and **never mutate** an existing lesson or decision. Conflict->supersede
  resolution is explicitly **deferred** to a later, separately-reviewed slice.

Safety contract (mirrors the lifecycle / merge apply paths):
- **dry-run is the default** and writes nothing - it returns the plan only;
- **apply without confirm fails closed**: it reports ``requires_confirmation``
  and writes nothing;
- a **confirmed** apply imports only the ``import``-classified candidates;
- the payload and audit trail are **metadata only** - actions, reason codes,
  similarity scores, match ids and any newly-minted import id, never candidate
  or stored bodies.

This is intentionally a CLI / owner-only surface; it exposes no new agent-facing
mutation tool (no public/agent mutation surface).
"""

from __future__ import annotations

from typing import Any

from . import reconcile_proposal as _rp

APPLY_ACTION = "reconcile_import_apply"

# Per-candidate outcome codes (metadata only).
OUTCOME_PLANNED = "planned"
OUTCOME_PENDING = "pending_confirmation"
OUTCOME_IMPORTED = "imported"
OUTCOME_NOOP = "noop"
OUTCOME_FAILED = "failed"


def _load_existing(eng) -> list[dict[str, Any]]:
    """Read active lessons + decisions for classification (access-neutral)."""
    existing: list[dict[str, Any]] = []
    if hasattr(eng, "get_lessons"):
        try:
            existing.extend(eng.get_lessons(limit=None, _update_access=False) or [])
        except Exception:  # pragma: no cover - defensive
            pass
    if hasattr(eng, "get_decisions"):
        try:
            existing.extend(eng.get_decisions(limit=None, _update_access=False) or [])
        except Exception:  # pragma: no cover - defensive
            pass
    return existing


def apply_reconcile(
    eng,
    candidates: list[dict[str, Any]] | None,
    *,
    existing: list[dict[str, Any]] | None = None,
    source: str = "",
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply (or preview) an import-only reconcile over external candidates.

    Args:
        candidates: already-loaded external entries (dicts with ``summary`` for
            lessons, or ``question``/``choice`` for decisions).
        existing: optional pre-loaded store entries; defaults to the live store.
        source: provenance label (metadata only).
        confirm: required (with ``dry_run=False``) to actually import.
        dry_run: when True (default) nothing is written - the plan is returned.

    Returns a metadata-only payload (see module docstring for the contract).
    """
    candidates = [c for c in (candidates or []) if isinstance(c, dict)]
    if existing is None:
        existing = _load_existing(eng)

    proposal = _rp.build_reconcile_proposal(candidates, existing, source=source)
    verdicts = proposal.get("items", [])

    # action -> apply outcome counts (metadata only).
    counts = {"import": 0, "duplicate": 0, "conflict": 0, "skip": 0,
              "imported": 0, "failed": 0}
    items: list[dict[str, Any]] = []

    for idx, verdict in enumerate(verdicts):
        action = verdict.get("action", "skip")
        counts[action] = counts.get(action, 0) + 1
        outcome = OUTCOME_PLANNED if action == "import" else OUTCOME_NOOP
        items.append({
            "candidate_ref": idx,
            "action": action,
            "reason": verdict.get("reason", ""),
            "entry_type": verdict.get("entry_type", "unknown"),
            "best_score": verdict.get("best_score", 0.0),
            "match_id": verdict.get("match_id", ""),
            "outcome": outcome,
            "imported_id": "",
        })

    planned = [it for it in items if it["outcome"] == OUTCOME_PLANNED]

    # 1) Dry-run (default): preview only, write nothing.
    if dry_run:
        return _payload(
            dry_run=True, confirmed=bool(confirm), requires_confirmation=False,
            changed=False, status="dry_run", counts=counts, items=items,
            source=source,
        )

    # 2) Apply without confirm: fail closed.
    if not confirm:
        for it in planned:
            it["outcome"] = OUTCOME_PENDING
        return _payload(
            dry_run=False, confirmed=False, requires_confirmation=True,
            changed=False, status="confirmation_required", counts=counts,
            items=items, source=source,
        )

    # 3) Confirmed apply: import ONLY the import-classified candidates.
    changed_any = False
    for it in planned:
        candidate = candidates[it["candidate_ref"]]
        new_id = _import_one(eng, candidate, it["entry_type"], source)
        if new_id:
            it["outcome"] = OUTCOME_IMPORTED
            it["imported_id"] = new_id
            counts["imported"] += 1
            changed_any = True
        else:
            # add_* declined (e.g. its own dedup caught it) - a no-op, not a write.
            it["outcome"] = OUTCOME_NOOP
            it["reason"] = "import_declined"

    return _payload(
        dry_run=False, confirmed=True, requires_confirmation=False,
        changed=changed_any, status="applied", counts=counts, items=items,
        source=source,
    )


def _import_one(eng, candidate: dict[str, Any], entry_type: str, source: str) -> str:
    """Import a single candidate via the existing write API; return its new id.

    Returns ``""`` if the write was declined (e.g. the store's own dedup caught a
    duplicate) or raised - the apply path never crashes on a single bad item.
    """
    tool = str(candidate.get("source_tool") or source or "reconcile_apply")
    audit = getattr(eng, "_audit", None)
    original_log = getattr(audit, "log", None)
    try:
        # The generic add_lesson/add_decision audit path records a title/body
        # prefix. This owner apply path has a stricter metadata-only contract, so
        # suppress the underlying content-bearing audit and emit a metadata-only
        # reconcile_import record after a successful import.
        if callable(original_log):
            audit.log = lambda *args, **kwargs: None
        if entry_type == "decision":
            result = eng.add_decision(
                str(candidate.get("question") or ""),
                choice=str(candidate.get("choice") or ""),
                reasoning=str(candidate.get("reasoning") or ""),
                source_tool=tool,
                tier="staging",
            )
        else:
            result = eng.add_lesson(
                str(candidate.get("summary") or ""),
                domain=str(candidate.get("domain") or ""),
                detail=str(candidate.get("detail") or ""),
                source_tool=tool,
                tier="staging",
            )
    except Exception:  # pragma: no cover - defensive
        return ""
    finally:
        if callable(original_log):
            audit.log = original_log
    if isinstance(result, dict):
        if result.get("status") == "duplicate":
            return ""
        new_id = result.get("id")
        if isinstance(new_id, str) and new_id:
            if callable(original_log):
                original_log(
                    "write",
                    "knowledge/reconcile_import",
                    detail=f"{entry_type}:{new_id} source={source or 'memory_files'}",
                )
            return new_id
    return ""


def _payload(
    *,
    dry_run: bool,
    confirmed: bool,
    requires_confirmation: bool,
    changed: bool,
    status: str,
    counts: dict[str, int],
    items: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "action": APPLY_ACTION,
        "source": source,
        "dry_run": bool(dry_run),
        "confirmed": bool(confirmed),
        "requires_confirmation": bool(requires_confirmation),
        "changed": bool(changed),
        "status": status,
        "counts": counts,
        "items": items,
    }


def render_reconcile_apply_text(payload: dict[str, Any]) -> str:
    """Render the reconcile apply payload as an owner-facing, metadata-only digest."""
    counts = payload.get("counts", {})
    mode = "dry-run" if payload.get("dry_run") else "apply"
    lines = [
        f"Reconcile import {mode} - status: {payload.get('status')}"
        + (f"  source: {payload['source']}" if payload.get("source") else ""),
        f"  import: {counts.get('import', 0)}  "
        f"imported: {counts.get('imported', 0)}  "
        f"duplicate: {counts.get('duplicate', 0)}  "
        f"conflict: {counts.get('conflict', 0)}  "
        f"skip: {counts.get('skip', 0)}",
    ]
    if payload.get("requires_confirmation"):
        lines.append("  confirmation required - re-run with --commit --yes to apply.")
    for it in payload.get("items", [])[:50]:
        label = it.get("imported_id") or f"candidate#{it.get('candidate_ref')}"
        lines.append(
            f"    - [{it.get('outcome')}] {label} action={it.get('action')} "
            f"type={it.get('entry_type')} score={it.get('best_score')} "
            f"match={it.get('match_id') or 'none'}"
        )
    lines.append(
        "  import-only: duplicates / conflicts are surfaced but never mutate "
        "existing knowledge (conflict resolution is deferred)."
    )
    return "\n".join(lines)
