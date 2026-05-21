"""T2: recognition for new workflow shortcut tools."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round5_descriptions.scenarios_v5 import T2_SCENARIOS


SHORTCUT_TOOLS = {"wrap_up_session", "start_project"}


def run_t2(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = []
    for scenario in scenarios or T2_SCENARIOS:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        rows.append(
            {
                "id": scenario["id"],
                "group": scenario["group"],
                "user_input": scenario["user_input"],
                "expected": scenario["expected_tool"],
                "actual": actual.get("tool"),
                "correct": actual.get("tool") == scenario["expected_tool"],
                "reasoning": actual.get("reasoning", ""),
            }
        )

    wrap_items = [row for row in rows if row["expected"] == "wrap_up_session"]
    start_items = [row for row in rows if row["expected"] == "start_project"]
    wrap_accuracy = _accuracy(wrap_items)
    start_accuracy = _accuracy(start_items)
    return {
        "test": "T2",
        "scenario_count": len(rows),
        "correct": sum(1 for row in rows if row["correct"]),
        "accuracy": _accuracy(rows),
        "wrap_up_session_accuracy": wrap_accuracy,
        "start_project_accuracy": start_accuracy,
        "wrap_up_session_judgment": _three_band(wrap_accuracy, 0.80, 0.60),
        "start_project_judgment": _three_band(start_accuracy, 0.80, 0.60),
        "results": rows,
    }


def test_t2_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in T2_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_t2(FakeJudge())
    assert result["scenario_count"] == 10
    assert result["wrap_up_session_accuracy"] == 1.0
    assert result["start_project_accuracy"] == 1.0


def _accuracy(rows: list[dict[str, Any]]) -> float:
    return sum(1 for row in rows if row["correct"]) / len(rows) if rows else 0.0


def _three_band(value: float, pass_at: float, warn_at: float) -> str:
    if value >= pass_at:
        return "pass"
    if value >= warn_at:
        return "gray"
    return "fail"

