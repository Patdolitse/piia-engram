from __future__ import annotations

from collections import Counter


def test_scenario_dataset_has_required_shape():
    from experiments.benchmarks.scenarios import SCENARIOS, validate_scenarios

    validate_scenarios(SCENARIOS)

    assert len(SCENARIOS) == 30
    assert len({scenario["id"] for scenario in SCENARIOS}) == 30

    counts = Counter(scenario["category"] for scenario in SCENARIOS)
    for category in (
        "remember_lesson",
        "remember_decision",
        "recall",
        "cleanup",
        "inherit",
        "sync",
        "advanced",
    ):
        assert counts[category] >= 3

    difficulty_counts = Counter(scenario["difficulty"] for scenario in SCENARIOS)
    assert difficulty_counts["hard"] >= 5


def test_atomic_tool_parser_finds_current_mcp_surface():
    from experiments.benchmarks.llm_judge import load_atomic_tool_descriptions

    tools = load_atomic_tool_descriptions()
    names = {tool["name"] for tool in tools}

    assert len(tools) == 37
    assert {"add_lesson", "add_decision", "search_knowledge", "export_engram"} <= names


def test_rule_based_judge_handles_main_intents_without_expected_labels():
    from experiments.benchmarks.rule_based_judge import (
        choose_atomic_tool,
        choose_coordinator_tool,
    )

    assert choose_atomic_tool("帮我找一下关于鉴权的经验") == "search_knowledge"
    assert choose_coordinator_tool("帮我找一下关于鉴权的经验") == "recall"
    assert choose_atomic_tool("新对话开始，加载我的上下文") == "get_user_context"
    assert choose_coordinator_tool("新对话开始，加载我的上下文") == "sync"
    assert choose_atomic_tool("导出 Engram 数据为 JSON") == "export_engram"
    assert choose_coordinator_tool("导出 Engram 数据为 JSON") == "fallback"


def test_internal_classifier_evaluation_uses_original_coordinator_logic():
    from experiments.benchmarks.run_benchmark import evaluate_internal_classifier
    from experiments.benchmarks.scenarios import SCENARIOS

    result = evaluate_internal_classifier(SCENARIOS)
    kind_mismatches = {
        item["id"]: item
        for item in result["kind_results"]
        if not item["correct"]
    }

    assert result["kind_accuracy"] < 1.0
    assert "S07" in kind_mismatches
    assert kind_mismatches["S07"]["actual"] == "decision"
