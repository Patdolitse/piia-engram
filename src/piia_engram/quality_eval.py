"""Knowledge quality evaluation — metadata-only rejection criteria (Task 11).

A *conservative, structural* gate for candidate lessons/decisions/playbooks. It
deliberately does NOT judge truth or semantics — that is the job of human review
and the optional Claude/DeepSeek double-review (see
``docs/specs/knowledge-quality-evaluation.md``). This helper only catches the
mechanical "this should never have been a durable memory" cases using metadata
and shape, so reviewers spend attention on real candidates.

Pure functions, stdlib only, no store access. Mirrors the conservative intent of
the existing extraction scorer (``context._assess_extraction_candidate``) but is
standalone and testable with fixtures.
"""

from __future__ import annotations

import re
from typing import Any

# Below these lengths a candidate is too thin to be durable knowledge.
MIN_LESSON_SUMMARY_LEN = 15
MIN_DECISION_CHOICE_LEN = 10

# Transient / debugging markers that should not become long-term memory.
# Matched on word boundaries so legitimate paths/words (e.g. "E:/Temp",
# "template", "attempt") do not false-positive. "tmp"/"temp" are intentionally
# omitted — they collide with paths far too often for a dev tool.
_TRANSIENT_MARKERS = (
    "todo",
    "fixme",
    "temporary",
    "debugging",
    "scratch",
    "ignore this",
    "test123",
    "asdf",
    "placeholder",
)
_TRANSIENT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in _TRANSIENT_MARKERS) + r")\b"
)

_QUESTION_RE = re.compile(r"[?？]\s*$")

# Mid-sentence fragments: a long summary chopped into segments yields pieces
# that open with a closing/joining punctuation, a bare number remnant
# ("2%）"), or a lone ascii letter ("x …"). Mirrors the extraction scorer's
# truncated_fragment check (context._assess_extraction_candidate).
_TRUNCATED_RES = (
    re.compile(r"^[)\]）】>》→、，。；：%…·:]"),
    re.compile(r"^\d+(?:\.\d+)?\s*[%)）]"),
    # lone ascii letter + space + CJK = chopped remnant ("x 孤儿改动…"); not an
    # English article opener ("a perfectly…"), which is ascii-then-ascii.
    re.compile(r"^[a-z]\s+[^\x00-\x7f]"),
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _looks_truncated(text: str) -> bool:
    t = text.strip()
    return bool(t) and any(rx.match(t) for rx in _TRUNCATED_RES)


def _has_transient_marker(*texts: str) -> bool:
    blob = " ".join(t.lower() for t in texts)
    return bool(_TRANSIENT_RE.search(blob))


def evaluate_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one candidate. Returns {accept, reasons, warnings, entry_type}.

    ``reasons`` are hard rejections; ``warnings`` are soft signals that do not by
    themselves reject but should be surfaced to the reviewer. ``accept`` is True
    iff there are no hard rejection reasons.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(entry, dict):
        return {"accept": False, "reasons": ["not_a_dict"], "warnings": [],
                "entry_type": "unknown"}

    # Infer type from shape.
    is_decision = "choice" in entry or "question" in entry
    is_playbook = "steps" in entry or "triggers" in entry
    entry_type = "decision" if is_decision else "playbook" if is_playbook else "lesson"

    summary = _text(entry.get("summary"))
    detail = _text(entry.get("detail"))
    question = _text(entry.get("question"))
    choice = _text(entry.get("choice"))
    reasoning = _text(entry.get("reasoning"))

    # --- hard rejections ---------------------------------------------------
    if entry_type == "lesson":
        if len(summary) < MIN_LESSON_SUMMARY_LEN:
            reasons.append("too_short")
        if _QUESTION_RE.search(summary) and not detail:
            reasons.append("open_question")  # a bare question is not a lesson
    elif entry_type == "decision":
        if len(choice) < MIN_DECISION_CHOICE_LEN:
            reasons.append("no_clear_choice")
        if not question:
            warnings.append("missing_question")
        if not reasoning:
            warnings.append("missing_reasoning")
    elif entry_type == "playbook":
        steps = entry.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            reasons.append("too_few_steps")
        triggers = entry.get("triggers")
        if not isinstance(triggers, list) or not any(_text(t) for t in triggers):
            warnings.append("missing_triggers")

    if _has_transient_marker(summary, detail, choice, question, reasoning):
        reasons.append("transient_marker")

    # A chopped mid-sentence fragment is never durable knowledge, regardless of
    # length. Check the primary content field for each type.
    primary = summary if entry_type == "lesson" else (
        choice if entry_type == "decision" else _text(entry.get("title"))
    )
    if _looks_truncated(primary):
        reasons.append("truncated_fragment")

    # --- soft warnings (metadata only) -------------------------------------
    domain = _text(entry.get("domain"))
    project = _text(entry.get("project"))
    prov = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
    prov_project = _text(prov.get("project")) if isinstance(prov, dict) else ""
    if not domain and not project and not prov_project:
        warnings.append("unclassified")  # no domain/project to retrieve it later

    if entry.get("tier") == "verified" and entry.get("approval_status") == "pending":
        warnings.append("verified_without_approval")  # inconsistent metadata

    return {
        "accept": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "entry_type": entry_type,
    }


def evaluate_batch(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate a batch; returns counts + per-item verdicts for review tooling."""
    verdicts = [evaluate_candidate(e) for e in (entries or [])]
    accepted = sum(1 for v in verdicts if v["accept"])
    return {
        "total": len(verdicts),
        "accepted": accepted,
        "rejected": len(verdicts) - accepted,
        "verdicts": verdicts,
    }


def build_quality_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate, metadata-only quality report for a set of candidates.

    Wraps :func:`evaluate_batch` and rolls the per-item verdicts up into reason
    and warning histograms plus a compact, content-free flagged list (id +
    entry_type + reasons/warnings only). This is a *reporting* helper for review
    tooling — it never promotes, never deletes, and never echoes summaries or
    other stored bodies, so it is safe to surface in aggregate views.
    """
    batch = evaluate_batch(entries)
    reason_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    flagged: list[dict[str, Any]] = []

    for entry, verdict in zip(entries or [], batch["verdicts"]):
        for reason in verdict["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for warning in verdict["warnings"]:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
        if verdict["reasons"] or verdict["warnings"]:
            entry_id = entry.get("id", "") if isinstance(entry, dict) else ""
            flagged.append({
                "id": entry_id,
                "entry_type": verdict["entry_type"],
                "accept": verdict["accept"],
                "reasons": list(verdict["reasons"]),
                "warnings": list(verdict["warnings"]),
            })

    return {
        "total": batch["total"],
        "accepted": batch["accepted"],
        "rejected": batch["rejected"],
        "reason_counts": reason_counts,
        "warning_counts": warning_counts,
        "flagged": flagged,
    }
