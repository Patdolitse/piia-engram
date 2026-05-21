"""Near-synonym atomic tool confusion test."""

from __future__ import annotations

from experiments.benchmarks.rule_based_judge import choose_atomic_tool


def run_near_synonym_test(scenarios: list[dict]) -> dict:
    near_scenarios = [scenario for scenario in scenarios if scenario["test_group"] == "E"]

    results = []
    grouped: dict[str, list[dict]] = {}
    for scenario in near_scenarios:
        actual = choose_atomic_tool(scenario["user_input"])
        item = {
            "id": scenario["id"],
            "group": scenario["near_synonym_group"],
            "user_input": scenario["user_input"],
            "expected": scenario["expected_atomic_tool"],
            "actual": actual,
            "correct": actual == scenario["expected_atomic_tool"],
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
    if accuracy < 0.80:
        decision = "协调器有价值"
    elif accuracy < 0.90:
        decision = "灰色地带"
    else:
        decision = "协调器是过度设计"

    return {
        "test": "E",
        "scenario_count": len(near_scenarios),
        "correct": correct_total,
        "accuracy": accuracy,
        "decision": decision,
        "groups": groups,
        "results": results,
    }
