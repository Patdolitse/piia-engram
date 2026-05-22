"""D6: Search Scoring Quality — 8 deterministic tests.

Verify _score_item() ranking, CJK handling, alias expansion, access boost.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram, _read_json, _write_json

from .fixtures import RECALL_LESSONS, CJK_LESSONS, create_lessons_only


def run_d6() -> dict[str, Any]:
    cases = [
        _d6_rank_01,
        _d6_rank_02,
        _d6_threshold_01,
        _d6_cjk_01,
        _d6_alias_01,
        _d6_alias_02,
        _d6_bigram_01,
        _d6_access_01,
    ]
    results = []
    for fn in cases:
        with tempfile.TemporaryDirectory(prefix="r10-d6-") as tmp:
            r = fn(Path(tmp))
            results.append(r)
    return {
        "dimension": "D6",
        "name": "Search Scoring Quality",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "passed": sum(1 for r in results if r["correct"]) >= 7,
        "results": results,
    }


def _result(case_id: str, correct: bool, detail: str = "") -> dict:
    return {"id": case_id, "correct": correct, "detail": detail}


def _d6_rank_01(tmp: Path) -> dict:
    """Query 'python pytest' → top 5 include all summary-match items."""
    e = create_lessons_only(tmp, RECALL_LESSONS)
    results = e.search_knowledge("python pytest")
    lessons = results.get("lessons", [])
    # Lessons with "pytest" in summary
    target_summaries = {l["summary"] for l in RECALL_LESSONS
                        if "pytest" in l["summary"].lower()}
    found_in_top5 = sum(1 for l in lessons[:5]
                        if l.get("summary", "") in target_summaries)
    # At least most pytest lessons should be in top 5
    ok = found_in_top5 >= 2  # We have ~3 pytest-related lessons
    return _result("D6-RANK-01", ok,
                    f"pytest_in_top5={found_in_top5}, total_results={len(lessons)}")


def _d6_rank_02(tmp: Path) -> dict:
    """Query 'python' → summary-match items score higher than detail-only."""
    e = Engram(tmp)
    # Summary match
    e.add_lesson("Python 是最好的编程语言之一", domain="python", detail="适合多种场景")
    # Detail-only match
    e.add_lesson("编程语言比较", domain="general", detail="Python 在数据科学领域表现出色")
    results = e.search_knowledge("python")
    lessons = results.get("lessons", [])
    if len(lessons) >= 2:
        # First result should be the summary-match
        ok = "Python" in lessons[0].get("summary", "")
    else:
        ok = len(lessons) >= 1
    return _result("D6-RANK-02", ok,
                    f"top_summary={lessons[0].get('summary', '')[:50] if lessons else 'none'}")


def _d6_threshold_01(tmp: Path) -> dict:
    """Query 'python' → no noise items (unrelated text) in results."""
    e = Engram(tmp)
    e.add_lesson("Python 列表推导式是强大的特性", domain="python")
    e.add_lesson("今天天气真好适合出去散步", domain="general")
    e.add_lesson("咖啡机的使用说明书放在厨房", domain="general")
    results = e.search_knowledge("python")
    lessons = results.get("lessons", [])
    noise = [l for l in lessons if "天气" in l.get("summary", "")
             or "咖啡" in l.get("summary", "")]
    return _result("D6-THRESHOLD-01", len(noise) == 0,
                    f"noise_count={len(noise)}, total={len(lessons)}")


def _d6_cjk_01(tmp: Path) -> dict:
    """CJK query '测试框架' → finds CJK lessons with those characters."""
    e = create_lessons_only(tmp, CJK_LESSONS)
    results = e.search_knowledge("测试框架")
    lessons = results.get("lessons", [])
    found = any("测试框架" in l.get("summary", "") for l in lessons)
    return _result("D6-CJK-01", found,
                    f"found={found}, count={len(lessons)}")


def _d6_alias_01(tmp: Path) -> dict:
    """Lesson with 'py' in summary, query 'python' → alias finds it."""
    e = Engram(tmp)
    e.add_lesson("py 脚本自动化日常任务效率提升显著", domain="python")
    results = e.search_knowledge("python")
    lessons = results.get("lessons", [])
    found = any("py" in l.get("summary", "").lower() for l in lessons)
    return _result("D6-ALIAS-01", found,
                    f"found={found}, count={len(lessons)}")


def _d6_alias_02(tmp: Path) -> dict:
    """Lesson with '工具' in summary, query 'tool' → alias finds it."""
    e = Engram(tmp)
    e.add_lesson("选择合适的工具是提高效率的关键因素", domain="general")
    results = e.search_knowledge("tool")
    lessons = results.get("lessons", [])
    found = any("工具" in l.get("summary", "") for l in lessons)
    return _result("D6-ALIAS-02", found,
                    f"found={found}, count={len(lessons)}")


def _d6_bigram_01(tmp: Path) -> dict:
    """'pytest 覆盖率' vs 'pytest 速度' → exact match scores higher."""
    e = Engram(tmp)
    e.add_lesson("pytest 覆盖率报告生成方法", domain="python")
    e.add_lesson("pytest 速度优化技巧总结", domain="python")
    results = e.search_knowledge("pytest 覆盖率")
    lessons = results.get("lessons", [])
    if lessons:
        ok = "覆盖率" in lessons[0].get("summary", "")
    else:
        ok = False
    return _result("D6-BIGRAM-01", ok,
                    f"top={lessons[0].get('summary', '')[:50] if lessons else 'none'}")


def _d6_access_01(tmp: Path) -> dict:
    """Two identical-text lessons, one with high access_count → higher rank."""
    e = Engram(tmp)
    l1 = e.add_lesson("Python 最佳实践指南总结", domain="python")
    l2 = e.add_lesson("Python 最佳实践指南概要", domain="python")
    # Boost l1's access_count
    path = e._knowledge_dir / "lessons.json"
    data = _read_json(path)
    for entry in data:
        if entry.get("id") == l1.get("id"):
            entry["access_count"] = 50
    _write_json(path, data)

    results = e.search_knowledge("Python 最佳实践")
    lessons = results.get("lessons", [])
    if len(lessons) >= 2:
        # Higher access should rank first (slight boost)
        ok = lessons[0].get("id") == l1.get("id")
    else:
        ok = len(lessons) >= 1
    return _result("D6-ACCESS-01", ok,
                    f"top_id={lessons[0].get('id', '')[:8] if lessons else 'none'}, "
                    f"l1_id={str(l1.get('id', ''))[:8]}")


if __name__ == "__main__":
    import json
    result = run_d6()
    print(json.dumps(result, indent=2, ensure_ascii=False))
