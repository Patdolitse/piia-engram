"""T1: near-synonym retest after tool description updates."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round5_descriptions.scenarios_v5 import T1_SCENARIOS


ROUND3_GROUP_BASELINES = {
    "lesson_decision_extract": 0.75,
    "search_relevant_similar": 1.0,
    "snapshot_context_user": 1.0,
}
ROUND3_OVERALL_BASELINE = 0.90


def run_t1(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scenario in scenarios or T1_SCENARIOS:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        row = {
            "id": scenario["id"],
            "group": scenario["group"],
            "source": scenario["source"],
            "user_input": scenario["user_input"],
            "expected": scenario["expected_tool"],
            "actual": actual.get("tool"),
            "correct": actual.get("tool") == scenario["expected_tool"],
            "reasoning": actual.get("reasoning", ""),
        }
        rows.append(row)
        grouped.setdefault(row["group"], []).append(row)

    groups = []
    for group, items in sorted(grouped.items()):
        correct = sum(1 for item in items if item["correct"])
        accuracy = correct / len(items)
        baseline = ROUND3_GROUP_BASELINES[group]
        groups.append(
            {
                "group": group,
                "total": len(items),
                "correct": correct,
                "accuracy": accuracy,
                "round3_baseline": baseline,
                "delta": accuracy - baseline,
                "judgment": _three_band(accuracy, pass_at=0.90, warn_at=0.75),
                "results": items,
            }
        )

    correct_total = sum(1 for row in rows if row["correct"])
    accuracy = correct_total / len(rows)
    return {
        "test": "T1",
        "scenario_count": len(rows),
        "correct": correct_total,
        "accuracy": accuracy,
        "round3_baseline": ROUND3_OVERALL_BASELINE,
        "delta": accuracy - ROUND3_OVERALL_BASELINE,
        "judgment": "pass" if all(group["accuracy"] >= 0.90 for group in groups) else "gray"
        if all(group["accuracy"] >= 0.75 for group in groups)
        else "fail",
        "groups": groups,
        "results": rows,
    }


def test_t1_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in T1_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_t1(FakeJudge())
    assert result["scenario_count"] == 20
    assert result["accuracy"] == 1.0
    assert all(group["accuracy"] == 1.0 for group in result["groups"])


def _three_band(value: float, pass_at: float, warn_at: float) -> str:
    if value >= pass_at:
        return "pass"
    if value >= warn_at:
        return "gray"
    return "fail"

