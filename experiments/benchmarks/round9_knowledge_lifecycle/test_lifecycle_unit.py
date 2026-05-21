"""T1: direct checks for knowledge lifecycle behavior."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from engram_core.core import Engram


def run_t1() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="engram-round9-lifecycle-") as tmp:
        engram = Engram(Path(tmp))
        review_results = _run_review_cases(engram, Path(tmp))

    with tempfile.TemporaryDirectory(prefix="engram-round9-stale-") as tmp:
        engram = Engram(Path(tmp))
        stale_results = _run_stale_cases(engram, Path(tmp))

    with tempfile.TemporaryDirectory(prefix="engram-round9-health-") as tmp:
        engram = Engram(Path(tmp))
        health_results = _run_health_cases(engram, Path(tmp))

    context_results = _run_context_cases()

    groups = {
        "review_knowledge": review_results,
        "get_stale_knowledge": stale_results,
        "health_report": health_results,
        "user_context_warning": context_results,
    }
    all_rows = [row for rows in groups.values() for row in rows]
    return {
        "test": "T1",
        "scenario_count": len(all_rows),
        "correct": sum(1 for row in all_rows if row["correct"]),
        "passed": all(row["correct"] for row in all_rows),
        "groups": {
            name: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row["correct"]),
                "passed": all(row["correct"] for row in rows),
                "results": rows,
            }
            for name, rows in groups.items()
        },
        "results": all_rows,
    }


def _run_review_cases(engram: Engram, root: Path) -> list[dict[str, Any]]:
    lesson = engram.add_lesson("round9 alpha review knowledge entry", domain="lifecycle")
    old_review = (datetime.now() - timedelta(days=45)).isoformat()
    _patch_lesson(root, lesson["id"], {"last_reviewed": old_review, "access_count": 1})

    first = engram.review_knowledge(lesson["id"])
    missing = engram.review_knowledge("nonexistent_id")
    second = engram.review_knowledge(lesson["id"])

    return [
        {
            "id": "T1-REVIEW-01",
            "category": "review_knowledge",
            "input": "review existing lesson",
            "expected": "last_reviewed refreshed and access_count=2",
            "actual": f"last_reviewed_changed={first.get('last_reviewed') != old_review}, access_count={first.get('access_count')}",
            "correct": first.get("last_reviewed") != old_review and first.get("access_count") == 2,
        },
        {
            "id": "T1-REVIEW-02",
            "category": "review_knowledge",
            "input": "review nonexistent_id",
            "expected": "error",
            "actual": missing,
            "correct": isinstance(missing, dict) and "error" in missing,
        },
        {
            "id": "T1-REVIEW-03",
            "category": "review_knowledge",
            "input": "review same lesson again",
            "expected": 3,
            "actual": second.get("access_count"),
            "correct": second.get("access_count") == 3,
        },
    ]


def _run_stale_cases(engram: Engram, root: Path) -> list[dict[str, Any]]:
    stale = engram.add_lesson("round9 bravo stale item old review", domain="lifecycle")
    fresh = engram.add_lesson("round9 charlie fresh item current review", domain="lifecycle")
    stale_review = (datetime.now() - timedelta(days=60)).isoformat()
    _patch_lesson(root, stale["id"], {"last_reviewed": stale_review})

    stale_30 = engram.get_stale_knowledge(days=30)
    stale_90 = engram.get_stale_knowledge(days=90)
    stale_limit_0 = engram.get_stale_knowledge(days=30, limit=0)

    return [
        {
            "id": "T1-STALE-01",
            "category": "get_stale_knowledge",
            "input": "days=30",
            "expected": [stale["id"]],
            "actual": _ids(stale_30),
            "correct": _ids(stale_30) == [stale["id"]] and fresh["id"] not in _ids(stale_30),
        },
        {
            "id": "T1-STALE-02",
            "category": "get_stale_knowledge",
            "input": "days=90",
            "expected": [],
            "actual": _ids(stale_90),
            "correct": _ids(stale_90) == [],
        },
        {
            "id": "T1-STALE-03",
            "category": "get_stale_knowledge",
            "input": "days=30, limit=0",
            "expected": [],
            "actual": _ids(stale_limit_0),
            "correct": _ids(stale_limit_0) == [],
        },
    ]


def _run_health_cases(engram: Engram, root: Path) -> list[dict[str, Any]]:
    review = engram.add_lesson("round9 delta high access needs review", domain="lifecycle")
    archive = engram.add_lesson("round9 echo zero access archive candidate", domain="lifecycle")
    _patch_lesson(
        root,
        review["id"],
        {
            "last_reviewed": (datetime.now() - timedelta(days=60)).isoformat(),
            "access_count": 5,
        },
    )
    _patch_lesson(
        root,
        archive["id"],
        {
            "last_reviewed": (datetime.now() - timedelta(days=90)).isoformat(),
            "access_count": 0,
        },
    )
    health = engram.get_health_report()
    needing_review = [item["id"] for item in health.get("items_needing_review", [])]
    to_archive = [item["id"] for item in health.get("items_to_archive", [])]
    return [
        {
            "id": "T1-HEALTH-01",
            "category": "health_report",
            "input": "access_count=5, last_reviewed=60d",
            "expected": review["id"],
            "actual": needing_review,
            "correct": review["id"] in needing_review,
        },
        {
            "id": "T1-HEALTH-02",
            "category": "health_report",
            "input": "access_count=0, last_reviewed=90d",
            "expected": archive["id"],
            "actual": to_archive,
            "correct": archive["id"] in to_archive,
        },
    ]


def _run_context_cases() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="engram-round9-context-many-") as tmp:
        many = Engram(Path(tmp))
        _add_stale_lessons(
            many,
            Path(tmp),
            [
                "round9 foxtrot stale context first",
                "round9 golf stale context second",
                "round9 hotel stale context third",
                "round9 india stale context fourth",
                "round9 juliet stale context fifth",
                "round9 kilo stale context sixth",
            ],
        )
        many_stale_before = _stale_count(many.get_stale_knowledge(days=30, limit=None))
        many_context = many.generate_context()
        many_stale_after = _stale_count(many.get_stale_knowledge(days=30, limit=None))

    with tempfile.TemporaryDirectory(prefix="engram-round9-context-few-") as tmp:
        few = Engram(Path(tmp))
        _add_stale_lessons(
            few,
            Path(tmp),
            [
                "round9 lima stale context one",
                "round9 mike stale context two",
                "round9 november stale context three",
            ],
        )
        few_stale_before = _stale_count(few.get_stale_knowledge(days=30, limit=None))
        few_context = few.generate_context()
        few_stale_after = _stale_count(few.get_stale_knowledge(days=30, limit=None))

    return [
        {
            "id": "T1-CONTEXT-01",
            "category": "user_context_warning",
            "input": "6 stale lessons",
            "expected": "warning present",
            "actual": (
                f"warning_present={'stale_knowledge_warning' in many_context or '未复习' in many_context}; "
                f"stale_before={many_stale_before}; stale_after={many_stale_after}; context={many_context}"
            ),
            "correct": "stale_knowledge_warning" in many_context or "未复习" in many_context,
        },
        {
            "id": "T1-CONTEXT-02",
            "category": "user_context_warning",
            "input": "3 stale lessons",
            "expected": "warning absent",
            "actual": (
                f"warning_present={'stale_knowledge_warning' in few_context or '未复习' in few_context}; "
                f"stale_before={few_stale_before}; stale_after={few_stale_after}; context={few_context}"
            ),
            "correct": "stale_knowledge_warning" not in few_context and "未复习" not in few_context,
        },
    ]


def _add_stale_lessons(engram: Engram, root: Path, summaries: list[str]) -> None:
    old_review = (datetime.now() - timedelta(days=60)).isoformat()
    ids = []
    for index, summary in enumerate(summaries):
        result = engram.add_lesson(f"{summary} uniqr9{index}", domain="lifecycle")
        if result.get("status") == "duplicate":
            result = engram.add_lesson(
                f"round9-context-marker-{index} token{index} phase{index} note{index}",
                domain="lifecycle",
            )
        ids.append(result["id"])
    for lesson_id in ids:
        _patch_lesson(root, lesson_id, {"last_reviewed": old_review})


def _patch_lesson(root: Path, lesson_id: str, updates: dict[str, Any]) -> None:
    path = root / "knowledge" / "lessons.json"
    lessons = json.loads(path.read_text(encoding="utf-8"))
    for lesson in lessons:
        if lesson.get("id") == lesson_id:
            lesson.update(updates)
    path.write_text(json.dumps(lessons, ensure_ascii=False, indent=2), encoding="utf-8")


def _ids(stale_result: dict[str, Any]) -> list[str]:
    return [item["id"] for item in stale_result.get("lessons", []) + stale_result.get("decisions", [])]


def _stale_count(stale_result: dict[str, Any]) -> int:
    return len(stale_result.get("lessons", [])) + len(stale_result.get("decisions", []))


def test_t1_lifecycle() -> None:
    result = run_t1()
    assert result["groups"]["review_knowledge"]["correct"] == 3
    assert result["groups"]["get_stale_knowledge"]["correct"] == 3
    assert result["groups"]["health_report"]["correct"] == 2
    assert result["groups"]["user_context_warning"]["correct"] == 2
