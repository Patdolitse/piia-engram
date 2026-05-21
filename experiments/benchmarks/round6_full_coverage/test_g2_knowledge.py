"""G2: Knowledge recording and retrieval tool selection."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round6_full_coverage.scenarios_v6 import G2_SCENARIOS
from experiments.benchmarks.round6_full_coverage.test_g1_identity import _run_group


ROUND5_NEAR_SYNONYM_BASELINE = {
    "lesson_decision_extract": 1.00,
    "search_relevant_similar": 1.00,
    "snapshot_context_user": 1.00,
}


def run_g2(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _run_group("G2", scenarios or G2_SCENARIOS, judge)
    result["round5_near_synonym_baseline"] = ROUND5_NEAR_SYNONYM_BASELINE
    return result


def test_g2_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in G2_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_g2(FakeJudge())
    assert result["scenario_count"] == 30
    assert result["accuracy"] == 1.0
    assert all(item["correct"] == 2 for item in result["by_tool"])
