"""Conflict-reconciliation proposal & receipt (Phase 8) — dry-run, no writes.

``reconcile.py`` *imports* knowledge from external AI-tool memory/config files.
This module is the **proposal** layer that sits in front of that: given already
loaded candidate entries and the existing store entries, it classifies each
candidate as ``import`` / ``duplicate`` / ``conflict`` and emits a metadata-only
proposal plus a receipt — **without writing anything**. The owner (or a future
reviewed wiring of ``reconcile``) decides whether to act.

Design constraints (mirror the other Phase helpers):
- pure, stdlib only, side-effect free, no store access; caller passes entries.
- metadata only: the proposal/receipt carry ids, types, actions, similarity
  scores, and reason codes — never the candidate/existing bodies.
- conservative conflict detection: a *conflict* is a candidate decision whose
  question closely matches an existing decision but whose choice differs. Near
  duplicates are reported as ``duplicate`` (skip). Everything else is ``import``.
- never destructive: this proposes; it does not import, merge, or delete.
"""

from __future__ import annotations

import re
from typing import Any

from .storage import (
    CONFLICT_C_CEILING as CHOICE_DIVERGENCE_THRESHOLD,
    CONFLICT_Q_THRESHOLD as CONFLICT_QUESTION_THRESHOLD,
)

# Default similarity threshold for "this is the same item" (matches the spirit
# of reconcile.py's bigram dedup; tuned here on token Jaccard).
DUPLICATE_THRESHOLD = 0.6
# Questions this similar are "about the same decision"; if the choices differ
# that is a conflict, not a duplicate. These values are shared with retrieval,
# but reconcile scores use token-Jaccard while retrieval scores use token-F1.

_WORD_RE = re.compile(r"[0-9a-zA-Z_]+")


def _tokens(text: Any) -> set[str]:
    """Lowercase word tokens + individual CJK characters (robust across scripts)."""
    if not isinstance(text, str) or not text.strip():
        return set()
    lowered = text.lower()
    toks = set(_WORD_RE.findall(lowered))
    toks.update(ch for ch in lowered if "一" <= ch <= "鿿")
    return toks


def similarity(a: Any, b: Any) -> float:
    """Token Jaccard similarity in [0, 1]. Empty inputs → 0.0."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _entry_type(entry: dict[str, Any]) -> str:
    return "decision" if ("choice" in entry or "question" in entry) else "lesson"


def _candidate_text(entry: dict[str, Any]) -> str:
    return str(entry.get("summary") or entry.get("choice") or entry.get("question") or "")


def classify_candidate(
    candidate: dict[str, Any],
    existing: list[dict[str, Any]],
    *,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
) -> dict[str, Any]:
    """Classify one candidate against the existing store. Metadata-only result.

    Returns ``{action, reason, best_score, match_id, entry_type}`` where
    ``action`` is ``"import"`` | ``"duplicate"`` | ``"conflict"``.
    """
    if not isinstance(candidate, dict):
        return {"action": "skip", "reason": "not_a_dict", "best_score": 0.0,
                "match_id": "", "entry_type": "unknown"}

    ctype = _entry_type(candidate)
    ctext = _candidate_text(candidate)

    best_score = 0.0
    best_id = ""

    # Conflict check first (decisions only): same question, different choice.
    if ctype == "decision":
        c_question = candidate.get("question") or ""
        c_choice = candidate.get("choice") or ""
        for ex in existing:
            if not isinstance(ex, dict) or _entry_type(ex) != "decision":
                continue
            q_sim = similarity(c_question, ex.get("question"))
            if q_sim >= CONFLICT_QUESTION_THRESHOLD:
                choice_sim = similarity(c_choice, ex.get("choice"))
                if choice_sim < CHOICE_DIVERGENCE_THRESHOLD:
                    return {
                        "action": "conflict",
                        "reason": "same_question_different_choice",
                        "best_score": round(q_sim, 4),
                        "match_id": ex.get("id", "") if isinstance(ex.get("id"), str) else "",
                        "entry_type": ctype,
                    }

    # Duplicate check (any type): high text similarity to an existing entry.
    for ex in existing:
        if not isinstance(ex, dict):
            continue
        score = similarity(ctext, _candidate_text(ex))
        if score > best_score:
            best_score = score
            best_id = ex.get("id", "") if isinstance(ex.get("id"), str) else ""

    if best_score >= duplicate_threshold:
        return {"action": "duplicate", "reason": "near_duplicate",
                "best_score": round(best_score, 4), "match_id": best_id,
                "entry_type": ctype}

    return {"action": "import", "reason": "novel",
            "best_score": round(best_score, 4), "match_id": best_id,
            "entry_type": ctype}


def build_reconcile_proposal(
    candidates: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    *,
    source: str = "",
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
) -> dict[str, Any]:
    """Build a metadata-only reconciliation proposal + receipt (no writes).

    Returns::

        {
          "source": str,
          "scanned": int,
          "counts": {"import": n, "duplicate": n, "conflict": n, "skip": n},
          "items": [ {candidate_id, action, reason, best_score, match_id,
                      entry_type}, ... ],
          "receipt": {"source", "scanned", "proposed_import", "duplicates",
                      "conflicts", "duplicate_threshold", "applied": false},
        }

    ``applied`` is always ``false``: this layer never imports. A separate,
    explicit, reviewed step would act on the proposal.
    """
    counts = {"import": 0, "duplicate": 0, "conflict": 0, "skip": 0}
    items: list[dict[str, Any]] = []

    for cand in candidates or []:
        verdict = classify_candidate(cand, existing or [],
                                     duplicate_threshold=duplicate_threshold)
        counts[verdict["action"]] = counts.get(verdict["action"], 0) + 1
        cand_id = ""
        if isinstance(cand, dict) and isinstance(cand.get("id"), str):
            cand_id = cand["id"]
        items.append({
            "candidate_id": cand_id,
            "action": verdict["action"],
            "reason": verdict["reason"],
            "best_score": verdict["best_score"],
            "match_id": verdict["match_id"],
            "entry_type": verdict["entry_type"],
        })

    receipt = {
        "source": source,
        "scanned": len(candidates or []),
        "proposed_import": counts["import"],
        "duplicates": counts["duplicate"],
        "conflicts": counts["conflict"],
        "duplicate_threshold": duplicate_threshold,
        "applied": False,
    }
    return {
        "source": source,
        "scanned": len(candidates or []),
        "counts": counts,
        "items": items,
        "receipt": receipt,
    }
