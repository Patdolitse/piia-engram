"""T1: direct unit checks for domain softening."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from engram_core.core import Engram


INFER_CASES = [
    {
        "id": "T1-INFER-01",
        "text": "pytest configuration for Python project",
        "fallback": "fallback",
        "expected_contains": ["python"],
    },
    {
        "id": "T1-INFER-02",
        "text": "docker compose for node.js app",
        "fallback": "fallback",
        "expected_contains": ["docker", "javascript"],
    },
    {
        "id": "T1-INFER-03",
        "text": "git rebase strategy for architecture refactor",
        "fallback": "fallback",
        "expected_contains": ["git", "architecture"],
    },
    {
        "id": "T1-INFER-04",
        "text": "random unrelated text",
        "fallback": "fallback",
        "expected_exact": "fallback",
    },
    {
        "id": "T1-INFER-05",
        "text": "MCP tool server design pattern",
        "fallback": "fallback",
        "expected_contains": ["mcp", "architecture"],
    },
    {
        "id": "T1-INFER-06",
        "text": "SQL migration for Django",
        "fallback": "fallback",
        "expected_contains": ["database", "python"],
    },
]


def run_t1() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="engram-round7-domain-") as tmp:
        engram = Engram(Path(tmp))
        infer_results = _run_infer_cases(engram)
        filter_results = _run_filter_cases(engram)
        compatibility_results = _run_compatibility_cases(engram)
        domain_count_results = _run_domain_count_cases(engram)

    groups = {
        "infer": infer_results,
        "contains_filter": filter_results,
        "compatibility": compatibility_results,
        "domain_counts": domain_count_results,
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


def _run_infer_cases(engram: Engram) -> list[dict[str, Any]]:
    rows = []
    for case in INFER_CASES:
        actual = engram._infer_domain(case["text"], case["fallback"])  # noqa: SLF001 - benchmark target
        actual_parts = _domain_set(actual)
        if "expected_exact" in case:
            correct = actual == case["expected_exact"]
            expected = case["expected_exact"]
        else:
            expected_parts = set(case["expected_contains"])
            correct = expected_parts <= actual_parts
            expected = ",".join(case["expected_contains"])
        rows.append(
            {
                "id": case["id"],
                "category": "infer",
                "input": case["text"],
                "expected": expected,
                "actual": actual,
                "correct": correct,
            }
        )
    return rows


def _run_filter_cases(engram: Engram) -> list[dict[str, Any]]:
    marker = "round7 alpha pytest bucket includes two tags"
    lesson = engram.add_lesson(marker, domain="python,testing", detail="multi-domain filter case")
    lesson_id = lesson["id"]
    cases = [
        ("T1-FILTER-01", "python", True),
        ("T1-FILTER-02", "testing", True),
        ("T1-FILTER-03", "javascript", False),
        ("T1-FILTER-04", None, True),
    ]
    rows = []
    for case_id, domain, should_find in cases:
        found = any(item["id"] == lesson_id for item in engram.get_lessons(domain=domain, limit=None))
        rows.append(
            {
                "id": case_id,
                "category": "contains_filter",
                "input": f"domain={domain}",
                "expected": should_find,
                "actual": found,
                "correct": found is should_find,
            }
        )
    return rows


def _run_compatibility_cases(engram: Engram) -> list[dict[str, Any]]:
    single = engram.add_lesson(
        "round7 bravo legacy single tag lookup",
        domain="python",
        detail="single-domain compatibility case",
    )
    empty = engram.add_lesson(
        "round7 charlie blank tag remains unfiltered",
        domain="",
        detail="empty-domain compatibility case",
    )
    python_results = engram.get_lessons(domain="python", limit=None)
    rows = [
        {
            "id": "T1-COMPAT-01",
            "category": "compatibility",
            "input": "single domain lesson with domain=python",
            "expected": True,
            "actual": any(item["id"] == single["id"] for item in python_results),
            "correct": any(item["id"] == single["id"] for item in python_results),
        },
        {
            "id": "T1-COMPAT-02",
            "category": "compatibility",
            "input": "empty domain lesson with domain=python filter",
            "expected": False,
            "actual": any(item["id"] == empty["id"] for item in python_results),
            "correct": not any(item["id"] == empty["id"] for item in python_results),
        },
        {
            "id": "T1-COMPAT-03",
            "category": "compatibility",
            "input": "unfiltered lessons include empty-domain lesson",
            "expected": True,
            "actual": any(item["id"] == empty["id"] for item in engram.get_lessons(domain=None, limit=None)),
            "correct": any(item["id"] == empty["id"] for item in engram.get_lessons(domain=None, limit=None)),
        },
    ]
    return rows


def _run_domain_count_cases(engram: Engram) -> list[dict[str, Any]]:
    # Use a fresh instance/root so earlier T1 lessons do not affect expected counts.
    with tempfile.TemporaryDirectory(prefix="engram-round7-domain-count-") as tmp:
        local = Engram(Path(tmp))
        local.add_lesson("round7 delta split count first", domain="python,testing")
        local.add_lesson("round7 echo solo count second", domain="python")
        domains = local.get_domains()
    return [
        {
            "id": "T1-COUNT-01",
            "category": "domain_counts",
            "input": "python count",
            "expected": 2,
            "actual": domains.get("python", {}).get("project_count"),
            "correct": domains.get("python", {}).get("project_count") == 2,
        },
        {
            "id": "T1-COUNT-02",
            "category": "domain_counts",
            "input": "testing count",
            "expected": 1,
            "actual": domains.get("testing", {}).get("project_count"),
            "correct": domains.get("testing", {}).get("project_count") == 1,
        },
    ]


def _domain_set(value: str) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def test_t1_domain_softening() -> None:
    result = run_t1()
    assert result["groups"]["infer"]["correct"] == 6
    assert result["groups"]["contains_filter"]["correct"] == 4
    assert result["groups"]["compatibility"]["correct"] == 3
    assert result["groups"]["domain_counts"]["correct"] == 2
