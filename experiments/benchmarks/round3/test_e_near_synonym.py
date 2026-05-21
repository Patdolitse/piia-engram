"""Test E: near-synonym atomic tool confusion with real LLM judgments."""

from __future__ import annotations

from typing import Any


def run_test_e(scenarios: list[dict[str, Any]], judge: Any) -> dict[str, Any]:
    near_scenarios = [scenario for scenario in scenarios if scenario["test_group"] == "E"]
    results = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scenario in near_scenarios:
        actual = judge.judge_atomic(scenario["id"], scenario["user_input"])
        item = {
            "id": scenario["id"],
            "group": scenario["near_synonym_group"],
            "user_input": scenario["user_input"],
            "expected": scenario["expected_atomic_tool"],
            "actual": actual.get("tool"),
            "correct": actual.get("tool") == scenario["expected_atomic_tool"],
            "reasoning": actual.get("reasoning", ""),
        }
        results.append(item)
        grouped.setdefault(item["group"], []).append(item)

    groups = []
    for name, items in sorted(grouped.items()):
        correct = sum(1 for item in items if item["correct"])
        groups.append(
            {
                "group": name,
                "total": len(items),
                "correct": correct,
                "accuracy": correct / len(items),
                "results": items,
            }
        )
    correct_total = sum(1 for item in results if item["correct"])
    accuracy = correct_total / len(results) if results else 0.0
    if accuracy < 0.70:
        decision = "协调器有价值"
        judgment = "✅"
    elif accuracy < 0.85:
        decision = "灰色地带"
        judgment = "⚠️"
    else:
        decision = "协调器是过度设计"
        judgment = "❌"
    return {
        "test": "E",
        "scenario_count": len(results),
        "correct": correct_total,
        "accuracy": accuracy,
        "decision": decision,
        "judgment": judgment,
        "groups": groups,
        "results": results,
    }
