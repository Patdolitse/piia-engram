"""G1: Identity and project-context tool selection."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round6_full_coverage.scenarios_v6 import G1_SCENARIOS


def run_g1(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _run_group("G1", scenarios or G1_SCENARIOS, judge)


def test_g1_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in G1_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_g1(FakeJudge())
    assert result["scenario_count"] == 24
    assert result["accuracy"] == 1.0
    assert all(item["correct"] == 2 for item in result["by_tool"])


def _run_group(group: str, scenarios: list[dict[str, Any]], judge: Any) -> dict[str, Any]:
    rows = []
    for scenario in scenarios:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        rows.append(
            {
                "id": scenario["id"],
                "group": group,
                "category": scenario["category"],
                "user_input": scenario["user_input"],
                "expected": scenario["expected_tool"],
                "actual": actual.get("tool"),
                "correct": actual.get("tool") == scenario["expected_tool"],
                "votes": actual.get("votes", []),
                "reasoning": actual.get("reasoning", ""),
            }
        )
    return _summarize_group(group, rows)


def _summarize_group(group: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool = []
    for tool in sorted({row["expected"] for row in rows}):
        items = [row for row in rows if row["expected"] == tool]
        correct = sum(1 for row in items if row["correct"])
        by_tool.append(
            {
                "tool": tool,
                "total": len(items),
                "correct": correct,
                "accuracy": correct / len(items),
                "status": "pass" if correct >= 1 else "fail",
            }
        )
    correct_total = sum(1 for row in rows if row["correct"])
    return {
        "test": group,
        "scenario_count": len(rows),
        "correct": correct_total,
        "accuracy": correct_total / len(rows),
        "zero_of_two_tools": [item["tool"] for item in by_tool if item["correct"] == 0],
        "judgment": _group_judgment(correct_total / len(rows), by_tool),
        "by_tool": by_tool,
        "results": rows,
    }


def _group_judgment(accuracy: float, by_tool: list[dict[str, Any]]) -> str:
    zero_count = sum(1 for item in by_tool if item["correct"] == 0)
    if accuracy >= 0.90 and zero_count == 0:
        return "pass"
    if accuracy >= 0.80 and zero_count <= 2:
        return "gray"
    return "fail"
