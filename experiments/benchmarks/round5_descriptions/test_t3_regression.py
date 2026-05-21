"""T3: selected full-tool regression after description updates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round5_descriptions.scenarios_v5 import T3_SCENARIOS


REPO_ROOT = Path(__file__).resolve().parents[3]
SHORTCUT_TOOLS = {"wrap_up_session", "start_project"}


def load_round3_baseline() -> dict[str, bool]:
    baseline: dict[str, bool] = {}
    atomic_path = REPO_ROOT / "experiments" / "benchmarks" / "results_atomic.json"
    if atomic_path.exists():
        data = json.loads(atomic_path.read_text(encoding="utf-8"))
        for row in data.get("results", []):
            baseline[row["id"]] = bool(row.get("correct"))
    e_path = REPO_ROOT / "experiments" / "benchmarks" / "round3" / "results_e.json"
    if e_path.exists():
        data = json.loads(e_path.read_text(encoding="utf-8"))
        for row in data.get("results", []):
            baseline[row["id"]] = bool(row.get("correct"))
    return baseline


def run_t3(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    baseline = load_round3_baseline()
    rows = []
    for scenario in scenarios or T3_SCENARIOS:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        row = {
            "id": scenario["id"],
            "round3_id": scenario.get("round3_id", scenario["id"]),
            "category": scenario["category"],
            "source": scenario["source"],
            "user_input": scenario["user_input"],
            "expected": scenario["expected_tool"],
            "actual": actual.get("tool"),
            "correct": actual.get("tool") == scenario["expected_tool"],
            "round3_correct": baseline.get(scenario.get("round3_id", scenario["id"]), True),
            "shortcut_false_positive": actual.get("tool") in SHORTCUT_TOOLS
            and scenario["expected_tool"] not in SHORTCUT_TOOLS,
            "reasoning": actual.get("reasoning", ""),
        }
        row["regressed"] = row["round3_correct"] and not row["correct"]
        rows.append(row)

    correct = sum(1 for row in rows if row["correct"])
    regressions = [row for row in rows if row["regressed"]]
    shortcut_false_positive_count = sum(1 for row in rows if row["shortcut_false_positive"])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)

    return {
        "test": "T3",
        "scenario_count": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "regression_count": len(regressions),
        "shortcut_false_positive_count": shortcut_false_positive_count,
        "passed": len(regressions) == 0,
        "by_category": {
            key: {
                "total": len(items),
                "correct": sum(1 for item in items if item["correct"]),
                "accuracy": sum(1 for item in items if item["correct"]) / len(items),
            }
            for key, items in sorted(grouped.items())
        },
        "results": rows,
        "regressions": regressions,
    }


def test_t3_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in T3_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_t3(FakeJudge())
    assert result["scenario_count"] == 15
    assert result["regression_count"] == 0
    assert result["shortcut_false_positive_count"] == 0
