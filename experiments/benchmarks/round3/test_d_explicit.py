"""Test D: coordinator with explicit AI-provided kind/domain."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round3.domain_aliases import domain_matches


def run_test_d(scenarios: list[dict[str, Any]], judge: Any) -> dict[str, Any]:
    remember_scenarios = [
        scenario for scenario in scenarios if scenario["expected_coordinator_tool"] == "remember"
    ]
    results = []
    for scenario in remember_scenarios:
        actual = judge.judge_coordinator(scenario["id"], scenario["user_input"])
        tool_correct = actual.get("tool") == scenario["expected_coordinator_tool"]
        kind_correct = actual.get("kind") == scenario["expected_kind"]
        domain_correct = domain_matches(scenario["expected_domain"], actual.get("domain"))
        results.append(
            {
                "id": scenario["id"],
                "difficulty": scenario["difficulty"],
                "user_input": scenario["user_input"],
                "expected_tool": scenario["expected_coordinator_tool"],
                "actual_tool": actual.get("tool"),
                "tool_correct": tool_correct,
                "expected_kind": scenario["expected_kind"],
                "actual_kind": actual.get("kind"),
                "kind_correct": tool_correct and kind_correct,
                "expected_domain": scenario["expected_domain"],
                "actual_domain": actual.get("domain"),
                "domain_correct": tool_correct and domain_correct,
                "reasoning": actual.get("reasoning", ""),
            }
        )

    summary = {
        "easy_kind": _difficulty_accuracy(results, "easy", "kind_correct"),
        "medium_kind": _difficulty_accuracy(results, "medium", "kind_correct"),
        "hard_kind": _difficulty_accuracy(results, "hard", "kind_correct"),
        "easy_medium_kind": _accuracy(
            [item for item in results if item["difficulty"] in {"easy", "medium"}],
            "kind_correct",
        ),
        "domain": _accuracy(results, "domain_correct"),
        "tool": _accuracy(results, "tool_correct"),
    }
    return {
        "test": "D",
        "scenario_count": len(results),
        "summary": summary,
        "judgments": {
            "easy_kind": _judge_threshold(summary["easy_kind"], 0.95, 0.85),
            "medium_kind": _judge_threshold(summary["medium_kind"], 0.95, 0.85),
            "hard_kind": _judge_threshold(summary["hard_kind"], 0.90, 0.70),
            "domain": _judge_threshold(summary["domain"], 0.80, 0.60),
        },
        "results": results,
        "hard_results": [item for item in results if item["difficulty"] == "hard"],
    }


def _difficulty_accuracy(results: list[dict[str, Any]], difficulty: str, field: str) -> float | None:
    items = [item for item in results if item["difficulty"] == difficulty]
    if not items:
        return None
    return _accuracy(items, field)


def _accuracy(items: list[dict[str, Any]], field: str) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if item[field]) / len(items)


def _judge_threshold(value: float | None, pass_at: float, warn_at: float) -> str:
    if value is None:
        return "N/A"
    if value >= pass_at:
        return "✅"
    if value >= warn_at:
        return "⚠️"
    return "❌"
