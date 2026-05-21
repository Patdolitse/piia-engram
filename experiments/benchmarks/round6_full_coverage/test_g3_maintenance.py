"""G3: Maintenance, session, and workflow tool selection."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round6_full_coverage.scenarios_v6 import G3_SCENARIOS
from experiments.benchmarks.round6_full_coverage.test_g1_identity import _run_group


SHORTCUT_TOOLS = {"wrap_up_session", "start_project"}
ATOMIC_TOOLS_THAT_SHORTCUTS_CAN_CONFUSE = {
    "extract_session_insights",
    "save_project_snapshot",
    "get_knowledge_inheritance",
    "get_project_context",
}


def run_g3(judge: Any, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _run_group("G3", scenarios or G3_SCENARIOS, judge)
    rows = result["results"]
    result["shortcut_false_positive_count"] = sum(
        1 for row in rows if row["actual"] in SHORTCUT_TOOLS and row["expected"] not in SHORTCUT_TOOLS
    )
    result["shortcut_false_negative_count"] = sum(
        1 for row in rows if row["expected"] in SHORTCUT_TOOLS and row["actual"] not in SHORTCUT_TOOLS
    )
    result["shortcut_confusions"] = [
        row
        for row in rows
        if (row["actual"] in SHORTCUT_TOOLS and row["expected"] not in SHORTCUT_TOOLS)
        or (row["expected"] in SHORTCUT_TOOLS and row["actual"] not in SHORTCUT_TOOLS)
    ]
    return result


def test_g3_with_fake_judge() -> None:
    class FakeJudge:
        def judge_tool(self, scenario_id: str, user_input: str) -> dict[str, str]:
            scenario = next(item for item in G3_SCENARIOS if item["id"] == scenario_id)
            return {"tool": scenario["expected_tool"], "reasoning": "fake"}

    result = run_g3(FakeJudge())
    assert result["scenario_count"] == 24
    assert result["accuracy"] == 1.0
    assert result["shortcut_false_positive_count"] == 0
    assert result["shortcut_false_negative_count"] == 0
