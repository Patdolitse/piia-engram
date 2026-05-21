"""R1: onboarding seed data integrity."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from engram_core.core import Engram

from experiments.benchmarks.round4_regression.scenarios_r1 import ONBOARDING_SCENARIOS


def seed_onboarding_profile(engram: Engram, scenario: dict[str, Any]) -> dict[str, Any]:
    """Apply the non-interactive equivalent of setup wizard seed writes."""
    profile_updates = {
        "role": scenario["role"],
        "language": scenario["language"],
        "tech_stack": scenario["tech_stack"],
    }
    if not engram.get_profile().get("description"):
        profile_updates["description"] = f"常用技术栈：{scenario['tech_stack']}"
    engram.update_profile(profile_updates)

    lessons_added = 0
    for lesson in scenario["lessons"]:
        result = engram.add_lesson(lesson, domain="setup", source_tool="engram_setup")
        if result.get("status") != "duplicate":
            lessons_added += 1

    return {
        "profile": profile_updates,
        "lessons_added": lessons_added,
        "primary_search_query": scenario["primary_search_query"],
    }


def run_r1(
    judge: Any,
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario_list = scenarios or ONBOARDING_SCENARIOS
    rows: list[dict[str, Any]] = []
    language_totals = {"zh": 0, "en": 0}
    language_passed = {"zh": 0, "en": 0}

    for scenario in scenario_list:
        with tempfile.TemporaryDirectory(prefix="engram-r1-") as tmp:
            engram = Engram(root=Path(tmp) / "engram")
            seed = seed_onboarding_profile(engram, scenario)
            context = engram.generate_context()
            search_results = engram.search_knowledge(
                seed["primary_search_query"],
                scope="lessons",
                limit=5,
            )
            judgment = judge.judge_onboarding(
                scenario["id"],
                seed["profile"],
                context,
                search_results,
            )

        role_pass = bool(judgment.get("role_readable"))
        tech_pass = bool(judgment.get("tech_stack_readable"))
        lesson_pass = bool(judgment.get("lesson_searchable"))
        language_pass = bool(judgment.get("language_ok"))

        for group in scenario.get("language_groups", [scenario["language_group"]]):
            if group in language_totals:
                language_totals[group] += 1
                if language_pass:
                    language_passed[group] += 1

        rows.append(
            {
                "id": scenario["id"],
                "role": scenario["role"],
                "tech_stack": scenario["tech_stack"],
                "language": scenario["language"],
                "profile_written": seed["profile"],
                "lessons_added": seed["lessons_added"],
                "search_query": seed["primary_search_query"],
                "search_hit_count": len(search_results.get("lessons", [])),
                "judgment": judgment,
                "passes": {
                    "role_readable": role_pass,
                    "tech_stack_readable": tech_pass,
                    "lesson_searchable": lesson_pass,
                    "language_ok": language_pass,
                },
            }
        )

    summary = {
        "scenario_count": len(rows),
        "role_readable": sum(1 for row in rows if row["passes"]["role_readable"]),
        "tech_stack_readable": sum(1 for row in rows if row["passes"]["tech_stack_readable"]),
        "lesson_searchable": sum(1 for row in rows if row["passes"]["lesson_searchable"]),
        "language": {
            key: {
                "passed": language_passed[key],
                "total": language_totals[key],
                "ratio": _ratio(language_passed[key], language_totals[key]),
            }
            for key in language_totals
        },
    }
    summary["passed"] = (
        summary["role_readable"] == 5
        and summary["tech_stack_readable"] == 5
        and summary["lesson_searchable"] >= 4
        and summary["language"]["zh"]["passed"] >= 2
        and summary["language"]["en"]["passed"] >= 2
    )
    return {"scenario_count": len(rows), "rows": rows, "summary": summary}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0

