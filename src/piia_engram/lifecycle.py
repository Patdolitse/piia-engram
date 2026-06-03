"""Memory lifecycle & decay scoring (Phase 7) — proposal-only, never destructive.

As a memory store grows, not all entries stay equally useful. This module
assigns each entry a *decay score* from **metadata only** (freshness/age, access
count, tier, quality signals) and turns those scores into an **archive/prune
proposal report**. It is the graceful-degradation layer: it tells the owner what
*could* be tidied, ranked, with reasons — and stops there.

Hard invariants (enforced by construction and by tests):
- **Never auto-delete.** This module computes scores and proposals; it performs
  no mutation, no archival, no deletion. Acting on a proposal is a separate,
  explicit, owner-gated step (CLI ``--apply`` on the management surface, with
  confirmation), never something lifecycle scoring triggers.
- **Metadata only.** Scores and proposals are derived from age/access/tier/
  quality metadata. The report never echoes summaries, choices, or any stored
  body — only ids, types, scores, and reason codes.
- **Pure & deterministic.** stdlib only, side-effect free, no store access; the
  caller passes already-loaded entries. Same input + same ``now`` → same output.
- **Conservative for unknowns.** Missing/odd metadata never escalates an entry
  to a prune proposal; it lands in ``keep`` or ``review`` at most.

See ``docs/runbooks/memory-lifecycle.md`` for policy and the never-auto-delete
contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import provenance as _provenance

# Decay contributions are bounded weights that sum to a score in [0, 1].
# Freshness dominates; access and tier are secondary nudges. Tuned to be
# conservative: a fresh, accessed, verified entry scores ~0; a stale, never
# accessed, staging entry approaches 1.
_FRESHNESS_WEIGHT = {
    "fresh": 0.0,
    "aging": 0.35,
    "stale": 0.6,
    "unknown": 0.3,  # unknown age is a mild signal, never decisive
}
_MAX_ACCESS_FOR_DECAY = 5  # accessed >= this many times → no access-driven decay
_ACCESS_WEIGHT = 0.25
_STAGING_WEIGHT = 0.15  # staging (un-promoted) entries decay slightly faster
_QUALITY_WEIGHT = 0.10  # entry that fails the structural quality gate

# Proposal thresholds (on the [0,1] decay score).
ARCHIVE_THRESHOLD = 0.55
PRUNE_THRESHOLD = 0.8

PROPOSAL_KEEP = "keep"
PROPOSAL_REVIEW = "review"
PROPOSAL_ARCHIVE = "archive_candidate"
PROPOSAL_PRUNE = "prune_candidate"


def _entry_type(entry: dict[str, Any]) -> str:
    if "choice" in entry or "question" in entry:
        return "decision"
    if "steps" in entry or "triggers" in entry:
        return "playbook"
    return "lesson"


def _access_count(entry: dict[str, Any]) -> int:
    raw = entry.get("access_count")
    if isinstance(raw, bool):  # bool is an int subclass — exclude it explicitly
        return 0
    if isinstance(raw, int) and raw >= 0:
        return raw
    return 0


def score_entry(
    entry: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute a metadata-only decay score for one entry.

    Returns ``{id, entry_type, decay_score, freshness_status, age_days,
    access_count, tier, reasons}`` where ``decay_score`` is in [0, 1] (higher =
    more decayed) and ``reasons`` are the contributing factor codes. Never
    mutates ``entry``.
    """
    if not isinstance(entry, dict):
        return {
            "id": "", "entry_type": "unknown", "decay_score": 0.0,
            "freshness_status": "unknown", "age_days": None,
            "access_count": 0, "tier": "", "reasons": ["not_a_dict"],
        }

    fresh = _provenance.compute_freshness(entry, now=now)
    status = fresh.get("freshness_status", "unknown")
    reasons: list[str] = []
    score = 0.0

    fw = _FRESHNESS_WEIGHT.get(status, _FRESHNESS_WEIGHT["unknown"])
    if fw:
        score += fw
        reasons.append(f"freshness_{status}")

    access = _access_count(entry)
    if access < _MAX_ACCESS_FOR_DECAY:
        # Scale: 0 accesses → full access weight; near the cap → ~0.
        factor = (_MAX_ACCESS_FOR_DECAY - access) / _MAX_ACCESS_FOR_DECAY
        score += _ACCESS_WEIGHT * factor
        if access == 0:
            reasons.append("never_accessed")
        else:
            reasons.append("low_access")

    tier = entry.get("tier", "") if isinstance(entry.get("tier"), str) else ""
    if tier == "staging":
        score += _STAGING_WEIGHT
        reasons.append("staging")

    # Structural quality gate (metadata/shape only) — a failing entry decays a
    # touch faster, but quality alone never forces a prune (small weight).
    try:
        from . import quality_eval as _qe

        verdict = _qe.evaluate_candidate(entry)
        if not verdict.get("accept", True):
            score += _QUALITY_WEIGHT
            reasons.append("low_quality")
    except Exception:  # pragma: no cover - quality gate is best-effort
        pass

    score = round(min(1.0, max(0.0, score)), 4)
    return {
        "id": entry.get("id", "") if isinstance(entry.get("id"), str) else "",
        "entry_type": _entry_type(entry),
        "decay_score": score,
        "freshness_status": status,
        "age_days": fresh.get("age_days"),
        "access_count": access,
        "tier": tier,
        "reasons": reasons,
    }


def _proposal_for(scored: dict[str, Any]) -> str:
    """Map a scored entry to a proposed action (proposal-only, conservative)."""
    score = scored["decay_score"]
    if score < ARCHIVE_THRESHOLD:
        return PROPOSAL_KEEP
    if score < PRUNE_THRESHOLD:
        return PROPOSAL_ARCHIVE
    # Prune proposals are the most conservative bucket: only un-promoted
    # (staging) entries that were never accessed are ever *proposed* for pruning.
    # Everything else with a very high score is surfaced for review instead, so
    # verified/used knowledge is never proposed for deletion.
    if scored["tier"] == "staging" and scored["access_count"] == 0:
        return PROPOSAL_PRUNE
    return PROPOSAL_REVIEW


def build_lifecycle_proposal(
    entries: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a metadata-only archive/prune **proposal** over ``entries``.

    Returns::

        {
          "total": int,
          "scored": int,                       # entries actually scored (dicts)
          "counts": {keep, review, archive_candidate, prune_candidate},
          "proposals": [
             {id, entry_type, decay_score, proposal, freshness_status,
              age_days, access_count, tier, reasons}, ...   # sorted by score desc
          ],
          "invariant": "never_auto_delete",
        }

    Active entries only are considered for archival/pruning by the caller; this
    function scores whatever it is given and never filters or mutates. Items that
    are not dicts are counted in ``total`` but skipped (not scored).
    """
    proposals: list[dict[str, Any]] = []
    counts = {PROPOSAL_KEEP: 0, PROPOSAL_REVIEW: 0,
              PROPOSAL_ARCHIVE: 0, PROPOSAL_PRUNE: 0}

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        scored = score_entry(entry, now=now)
        proposal = _proposal_for(scored)
        scored["proposal"] = proposal
        counts[proposal] += 1
        proposals.append(scored)

    # Deterministic ordering: most-decayed first, ties broken by id.
    proposals.sort(key=lambda p: (-p["decay_score"], p["id"]))

    return {
        "total": len(entries or []),
        "scored": len(proposals),
        "counts": counts,
        "proposals": proposals,
        "invariant": "never_auto_delete",
    }


def select_archive_candidate_ids(
    report: dict[str, Any],
    *,
    requested_ids: list[str] | None = None,
) -> list[str]:
    """Return the ids eligible for an owner-confirmed soft archive.

    Eligible means the proposal is ``archive_candidate`` or ``prune_candidate``
    **and** the entry is neither in the ``verified`` tier (verified/trusted
    knowledge is never archived by this path) nor already in the ``archived``
    tier (already-archived entries are not re-proposed). When ``requested_ids``
    is given, the eligible set is intersected with it (order follows the
    report's most-decayed-first ordering).

    Pure: derives only from the metadata-only proposal report; mutates nothing.
    """
    requested = {str(i) for i in requested_ids} if requested_ids is not None else None
    eligible: list[str] = []
    for proposal in report.get("proposals", []):
        if proposal.get("proposal") not in (PROPOSAL_ARCHIVE, PROPOSAL_PRUNE):
            continue
        if proposal.get("tier") in {"verified", "archived"}:
            continue
        item_id = proposal.get("id")
        if not item_id:
            continue
        if requested is not None and item_id not in requested:
            continue
        eligible.append(item_id)
    return eligible


def render_lifecycle_text(report: dict[str, Any]) -> str:
    """Render a lifecycle proposal as an owner-facing, metadata-only digest."""
    counts = report.get("counts", {})
    lines = [
        "Memory lifecycle proposal (metadata only — nothing was changed):",
        f"  scored: {report.get('scored', 0)} / {report.get('total', 0)} entries",
        f"  keep: {counts.get(PROPOSAL_KEEP, 0)}  "
        f"review: {counts.get(PROPOSAL_REVIEW, 0)}  "
        f"archive: {counts.get(PROPOSAL_ARCHIVE, 0)}  "
        f"prune: {counts.get(PROPOSAL_PRUNE, 0)}",
    ]
    flagged = [p for p in report.get("proposals", [])
               if p["proposal"] in (PROPOSAL_ARCHIVE, PROPOSAL_PRUNE)]
    if flagged:
        lines.append("  candidates (most decayed first):")
        for p in flagged[:50]:
            label = p["id"] or "(no id)"
            lines.append(
                f"    - [{p['proposal']}] {label} type={p['entry_type']} "
                f"score={p['decay_score']} reasons={','.join(p['reasons'])}"
            )
    lines.append("  invariant: never_auto_delete — apply requires explicit owner action.")
    return "\n".join(lines)
