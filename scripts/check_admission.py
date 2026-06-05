"""Offline admission guard over candidate memories.

This is a read-only guard: it evaluates candidate entries against conservative
quality rules, duplicate similarity, and obvious conflict routing. It never
promotes, stores, deletes, or rewrites knowledge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from piia_engram import quality_eval  # noqa: E402
# This guard intentionally mirrors a small subset of storage heuristics without
# invoking any production write path; keep it read-only and metadata-only.
from piia_engram.storage import (  # noqa: E402
    CONFLICT_C_CEILING,
    CONFLICT_Q_THRESHOLD,
    SIMILARITY_DUPLICATE_THRESHOLD,
    _AFFIRMATION_MARKERS,
    _NEGATION_MARKERS,
)


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "admission_guard_v1.json"


def load_fixture(path: str | Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("admission guard fixture schema must be 1")
    if not isinstance(data.get("existing"), list):
        raise ValueError("admission guard fixture requires existing list")
    if not isinstance(data.get("candidates"), list) or not data["candidates"]:
        raise ValueError("admission guard fixture requires candidates")
    return data


def _entry_type(entry: dict[str, Any]) -> str:
    if "choice" in entry or "question" in entry:
        return "decision"
    if "steps" in entry or "triggers" in entry:
        return "playbook"
    return "lesson"


def _primary_text(entry: dict[str, Any]) -> str:
    if _entry_type(entry) == "decision":
        return f"{entry.get('question', '')} {entry.get('choice', '')}".strip()
    if _entry_type(entry) == "playbook":
        steps = entry.get("steps") if isinstance(entry.get("steps"), list) else []
        return " ".join([str(entry.get("title", "")), *(str(step) for step in steps)]).strip()
    return str(entry.get("summary") or "").strip()


def _tokens(text: str) -> set[str]:
    words = {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}
    cjk = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    words.update(cjk)
    words.update(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return words


def _similarity(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def _domain_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_domains = {d.strip() for d in str(left.get("domain") or left.get("project") or "").split(",") if d.strip()}
    right_domains = {d.strip() for d in str(right.get("domain") or right.get("project") or "").split(",") if d.strip()}
    return not left_domains or not right_domains or bool(left_domains & right_domains)


def _duplicate_of(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> str:
    ctype = _entry_type(candidate)
    ctext = _primary_text(candidate)
    for item in existing:
        if not isinstance(item, dict) or _entry_type(item) != ctype:
            continue
        if _similarity(ctext, _primary_text(item)) >= SIMILARITY_DUPLICATE_THRESHOLD:
            return str(item.get("id") or "")
    return ""


def _lesson_conflicts(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> list[str]:
    ctext = str(candidate.get("summary") or "")
    has_neg_c = any(marker in ctext.lower() for marker in _NEGATION_MARKERS)
    has_pos_c = any(marker in ctext.lower() for marker in _AFFIRMATION_MARKERS)
    conflicts: list[str] = []
    for item in existing:
        if not isinstance(item, dict) or _entry_type(item) != "lesson":
            continue
        if not _domain_overlap(candidate, item):
            continue
        text = str(item.get("summary") or "")
        shared = {token for token in _tokens(ctext) & _tokens(text) if len(token) >= 4}
        if not shared:
            continue
        has_neg_i = any(marker in text.lower() for marker in _NEGATION_MARKERS)
        has_pos_i = any(marker in text.lower() for marker in _AFFIRMATION_MARKERS)
        if (has_neg_c and has_pos_i) or (has_pos_c and has_neg_i):
            conflicts.append(str(item.get("id") or ""))
    return [item_id for item_id in conflicts if item_id]


def _decision_conflicts(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> list[str]:
    question = str(candidate.get("question") or "")
    choice = str(candidate.get("choice") or "")
    conflicts: list[str] = []
    for item in existing:
        if not isinstance(item, dict) or _entry_type(item) != "decision":
            continue
        if not _domain_overlap(candidate, item):
            continue
        q_sim = _similarity(question, str(item.get("question") or ""))
        if q_sim < CONFLICT_Q_THRESHOLD:
            continue
        c_sim = _similarity(choice, str(item.get("choice") or ""))
        if c_sim >= CONFLICT_C_CEILING:
            continue
        conflicts.append(str(item.get("id") or ""))
    return [item_id for item_id in conflicts if item_id]


def _conflicts_with(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> list[str]:
    if _entry_type(candidate) == "decision":
        return _decision_conflicts(candidate, existing)
    if _entry_type(candidate) == "lesson":
        return _lesson_conflicts(candidate, existing)
    return []


def evaluate_candidate_admission(
    candidate: dict[str, Any],
    existing: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a metadata-only admission verdict for one candidate."""
    existing = existing or []
    verdict = quality_eval.evaluate_candidate(candidate)
    result: dict[str, Any] = {
        "id": str(candidate.get("id") or ""),
        "entry_type": verdict["entry_type"],
        "reasons": list(verdict["reasons"]),
        "warnings": list(verdict["warnings"]),
    }

    if not verdict["accept"]:
        result["action"] = "reject"
        result["suggested_action"] = "keep_in_staging"
        return result

    duplicate = _duplicate_of(candidate, existing)
    if duplicate:
        result["action"] = "duplicate"
        result["suggested_action"] = "skip_duplicate"
        result["duplicate_of"] = duplicate
        return result

    conflicts = _conflicts_with(candidate, existing)
    if conflicts:
        result["action"] = "review_update"
        result["suggested_action"] = "update_knowledge"
        result["conflict_with"] = conflicts
        return result

    if result["warnings"]:
        result["action"] = "stage"
        result["suggested_action"] = "human_review"
        return result

    result["action"] = "accept"
    result["suggested_action"] = "eligible_for_review_promotion"
    return result


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    existing = [item for item in fixture.get("existing") or [] if isinstance(item, dict)]
    results = []
    failed: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for candidate in fixture.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        result = evaluate_candidate_admission(candidate, existing)
        expected = candidate.get("_expected_action")
        if expected:
            result["expected_action"] = str(expected)
            if result["action"] != expected:
                failed.append({
                    "id": result["id"],
                    "expected": str(expected),
                    "actual": str(result["action"]),
                })
        counts[result["action"]] = counts.get(result["action"], 0) + 1
        results.append(result)

    return {
        "schema": 1,
        "guard": str(fixture.get("guard") or "admission_guard_v1"),
        "public_safe": bool(fixture.get("public_safe")),
        "candidate_count": len(results),
        "overall_passed": not failed,
        "failed_expectations": failed,
        "action_counts": dict(sorted(counts.items())),
        "results": results,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Admission Guard v1",
        "",
        f"Overall: {'PASS' if summary.get('overall_passed') else 'FAIL'}",
        f"Candidates: {summary.get('candidate_count', 0)}",
        "",
        "| Action | Count |",
        "|---|---:|",
    ]
    for action, count in (summary.get("action_counts") or {}).items():
        lines.append(f"| {action} | {count} |")
    lines.extend(["", "| Candidate | Action | Suggested |", "|---|---|---|"])
    for result in summary.get("results") or []:
        lines.append(
            f"| {result.get('id')} | {result.get('action')} | "
            f"{result.get('suggested_action', '')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    summary = evaluate_fixture(load_fixture(args.fixture))
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(summary), end="")
    return 0 if summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
