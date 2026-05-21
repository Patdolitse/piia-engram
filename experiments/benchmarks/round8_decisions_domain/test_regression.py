"""T2: sampled Round 6 tool-selection regression for decision-domain changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round8_decisions_domain.scenarios_v8 import T2_SCENARIOS


ROUND8_DIR = Path(__file__).resolve().parent
ROUND6_DIR = ROUND8_DIR.parent / "round6_full_coverage"


def load_round6_baseline() -> dict[str, bool]:
    baseline: dict[str, bool] = {}
    for path in ROUND6_DIR.glob("results_g*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("results", []):
            baseline[row["id"]] = bool(row.get("correct"))
    return baseline


def run_t2(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    baseline = load_round6_baseline()
    rows = []
    for scenario in scenarios or T2_SCENARIOS:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        round6_id = scenario["round6_id"]
        correct = actual.get("tool") == scenario["expected_tool"]
        rows.append(
            {
                "id": scenario["id"],
                "round6_id": round6_id,
                "source_group": round6_id.split("-", 1)[0],
                "category": scenario["category"],
                "user_input": scenario["user_input"],
                "expected": scenario["expected_tool"],
                "actual": actual.get("tool"),
                "correct": correct,
                "round6_correct": baseline.get(round6_id),
                "regressed": baseline.get(round6_id) is True and not correct,
                "votes": actual.get("votes", []),
                "reasoning": actual.get("reasoning", ""),
            }
        )

    correct_total = sum(1 for row in rows if row["correct"])
    baseline_correct = sum(1 for row in rows if row["round6_correct"])
    regressions = [row for row in rows if row["regressed"]]
    return {
        "test": "T2",
        "scenario_count": len(rows),
        "correct": correct_total,
        "accuracy": correct_total / len(rows),
        "round6_same_scenario_correct": baseline_correct,
        "round6_same_scenario_accuracy": baseline_correct / len(rows),
        "regression_count": len(regressions),
        "passed": correct_total >= 19 and correct_total >= baseline_correct and not regressions,
        "by_source_group": _by_source_group(rows),
        "results": rows,
        "regressions": regressions,
    }


def _by_source_group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["source_group"], []).append(row)
    return {
        group: {
            "total": len(items),
            "correct": sum(1 for item in items if item["correct"]),
            "accuracy": sum(1 for item in items if item["correct"]) / len(items),
            "round6_correct": sum(1 for item in items if item["round6_correct"]),
        }
        for group, items in sorted(grouped.items())
    }


def test_t2_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in T2_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_t2(FakeJudge())
    assert result["scenario_count"] == 20
    assert result["correct"] == 20
