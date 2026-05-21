"""G4: no-tool and missing-parameter boundary scenarios."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round6_full_coverage.scenarios_v6 import G4_SCENARIOS


def run_g4(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = []
    for scenario in scenarios or G4_SCENARIOS:
        actual = judge.judge_tool(scenario["id"], scenario["user_input"])
        rows.append(
            {
                "id": scenario["id"],
                "boundary_type": scenario["boundary_type"],
                "user_input": scenario["user_input"],
                "expected": scenario["expected_tool"],
                "actual": actual.get("tool"),
                "correct": actual.get("tool") == scenario["expected_tool"],
                "votes": actual.get("votes", []),
                "reasoning": actual.get("reasoning", ""),
            }
        )

    no_tool = [row for row in rows if row["boundary_type"] == "no_tool"]
    missing = [row for row in rows if row["boundary_type"] == "missing_params"]
    no_tool_correct = sum(1 for row in no_tool if row["correct"])
    missing_correct = sum(1 for row in missing if row["correct"])
    correct_total = sum(1 for row in rows if row["correct"])
    return {
        "test": "G4",
        "scenario_count": len(rows),
        "correct": correct_total,
        "accuracy": correct_total / len(rows),
        "no_tool": {
            "total": len(no_tool),
            "correct": no_tool_correct,
            "accuracy": no_tool_correct / len(no_tool),
            "judgment": _boundary_judgment(no_tool_correct, pass_at=4, gray_at=3),
        },
        "missing_params": {
            "total": len(missing),
            "correct": missing_correct,
            "accuracy": missing_correct / len(missing),
            "judgment": _boundary_judgment(missing_correct, pass_at=3, gray_at=2),
        },
        "judgment": _overall_boundary_judgment(no_tool_correct, missing_correct),
        "results": rows,
    }


def test_g4_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in G4_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_g4(FakeJudge())
    assert result["scenario_count"] == 10
    assert result["no_tool"]["correct"] == 5
    assert result["missing_params"]["correct"] == 5


def _boundary_judgment(correct: int, pass_at: int, gray_at: int) -> str:
    if correct >= pass_at:
        return "pass"
    if correct >= gray_at:
        return "gray"
    return "fail"


def _overall_boundary_judgment(no_tool_correct: int, missing_correct: int) -> str:
    no_tool = _boundary_judgment(no_tool_correct, pass_at=4, gray_at=3)
    missing = _boundary_judgment(missing_correct, pass_at=3, gray_at=2)
    if no_tool == "pass" and missing == "pass":
        return "pass"
    if "fail" not in {no_tool, missing}:
        return "gray"
    return "fail"
