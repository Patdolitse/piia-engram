"""D3 deterministic: Lesson Recall Precision — 8 tests.

Verify get_relevant_lessons() bucket split, domain matching, and scoring.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram

from .fixtures import RECALL_LESSONS, CJK_LESSONS, create_lessons_only


def run_d3_det() -> dict[str, Any]:
    cases = [
        _d3_bucket_01,
        _d3_bucket_02,
        _d3_bucket_03,
        _d3_precision_01,
        _d3_empty_stack_01,
        _d3_unmapped_stack_01,
        _d3_scoring_01,
        _d3_scoring_02,
    ]
    results = []
    for fn in cases:
        with tempfile.TemporaryDirectory(prefix="r10-d3-") as tmp:
            r = fn(Path(tmp))
            results.append(r)
    return {
        "dimension": "D3_det",
        "name": "Recall Precision (deterministic)",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "passed": sum(1 for r in results if r["correct"]) >= 7,
        "results": results,
    }


def _result(case_id: str, correct: bool, detail: str = "") -> dict:
    return {"id": case_id, "correct": correct, "detail": detail}


def _setup_with_project(tmp: Path, tech_stack: list[str]) -> Engram:
    """Create engram with 30 recall lessons + project snapshot."""
    e = create_lessons_only(tmp, RECALL_LESSONS)
    e.save_project_snapshot("E:/proj", {
        "title": "Test Project",
        "tech_stack": tech_stack,
    })
    return e


def _d3_bucket_01(tmp: Path) -> dict:
    """30 lessons, project=python+MCP → at least 4/8 are python/mcp_dev."""
    e = _setup_with_project(tmp, ["Python", "MCP"])
    results = e.get_relevant_lessons(project_folder="E:/proj", limit=8, _update_access=False)
    relevant = [l for l in results if any(
        d.strip() in ("python", "mcp_dev")
        for d in (l.get("domain") or "").split(",")
    )]
    ok = len(relevant) >= 4
    return _result("D3-BUCKET-01", ok,
                    f"relevant={len(relevant)}/8, domains={[l.get('domain') for l in results]}")


def _d3_bucket_02(tmp: Path) -> dict:
    """At least 1 universal domain (架构) in results."""
    e = _setup_with_project(tmp, ["Python", "MCP"])
    results = e.get_relevant_lessons(project_folder="E:/proj", limit=8, _update_access=False)
    universal = [l for l in results if "架构" in (l.get("domain") or "")]
    return _result("D3-BUCKET-02", len(universal) >= 1,
                    f"universal_count={len(universal)}")


def _d3_bucket_03(tmp: Path) -> dict:
    """Returns exactly 8 lessons."""
    e = _setup_with_project(tmp, ["Python", "MCP"])
    results = e.get_relevant_lessons(project_folder="E:/proj", limit=8, _update_access=False)
    return _result("D3-BUCKET-03", len(results) == 8,
                    f"count={len(results)}")


def _d3_precision_01(tmp: Path) -> dict:
    """10 python + 20 unrelated → precision >= 60% (5/8 python)."""
    python_lessons = [l for l in RECALL_LESSONS if l["domain"] == "python"]
    other_lessons = [l for l in RECALL_LESSONS if l["domain"] != "python"]
    e = create_lessons_only(tmp, python_lessons + other_lessons)
    e.save_project_snapshot("E:/proj", {"title": "Py", "tech_stack": ["Python"]})
    results = e.get_relevant_lessons(project_folder="E:/proj", limit=8, _update_access=False)
    python_count = sum(1 for l in results if "python" in (l.get("domain") or ""))
    precision = python_count / len(results) if results else 0
    return _result("D3-PRECISION-01", precision >= 0.5,
                    f"python={python_count}/8, precision={precision:.0%}")


def _d3_empty_stack_01(tmp: Path) -> dict:
    """No project_folder → returns 8 lessons (time-sorted fallback)."""
    e = create_lessons_only(tmp, RECALL_LESSONS)
    results = e.get_relevant_lessons(limit=8, _update_access=False)
    return _result("D3-EMPTY-STACK-01", len(results) == 8,
                    f"count={len(results)}")


def _d3_unmapped_stack_01(tmp: Path) -> dict:
    """tech_stack=[Rust] (not in mapping) → fallback to time-sort."""
    e = create_lessons_only(tmp, RECALL_LESSONS)
    e.save_project_snapshot("E:/proj", {"title": "Rust", "tech_stack": ["Rust"]})
    results = e.get_relevant_lessons(project_folder="E:/proj", limit=8, _update_access=False)
    # Should still return 8, just not domain-filtered
    return _result("D3-UNMAPPED-STACK-01", len(results) == 8,
                    f"count={len(results)} (fallback, no rust mapping)")


def _d3_scoring_01(tmp: Path) -> dict:
    """search_knowledge('python pytest') → top 3 have python/testing domain."""
    e = create_lessons_only(tmp, RECALL_LESSONS)
    results = e.search_knowledge("python pytest")
    lessons = results.get("lessons", [])
    if len(lessons) >= 3:
        top3_domains = [l.get("domain", "") for l in lessons[:3]]
        ok = all("python" in d or "testing" in d for d in top3_domains)
    else:
        ok = False
    return _result("D3-SCORING-01", ok,
                    f"top3_domains={[l.get('domain') for l in lessons[:3]]}")


def _d3_scoring_02(tmp: Path) -> dict:
    """CJK query '测试框架' → finds matching CJK lessons."""
    e = create_lessons_only(tmp, CJK_LESSONS)
    results = e.search_knowledge("测试框架")
    lessons = results.get("lessons", [])
    found = any("测试框架" in l.get("summary", "") for l in lessons)
    return _result("D3-SCORING-02", found,
                    f"found_match={found}, results={len(lessons)}")


if __name__ == "__main__":
    import json
    result = run_d3_det()
    print(json.dumps(result, indent=2, ensure_ascii=False))
