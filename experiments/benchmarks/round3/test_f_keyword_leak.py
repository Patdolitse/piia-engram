"""Test F: keyword-replacement leak detection."""

from __future__ import annotations

from typing import Any

from experiments.benchmarks.round3.domain_aliases import domain_matches


def run_test_f(scenarios: list[dict[str, Any]], judge: Any) -> dict[str, Any]:
    by_id = {scenario["id"]: scenario for scenario in scenarios}
    variants = [scenario for scenario in scenarios if scenario["test_group"] == "F"]
    pairs = []
    for variant in variants:
        original = by_id[variant["variant_of"]]
        original_actual = judge.judge_coordinator(original["id"], original["user_input"])
        variant_actual = judge.judge_coordinator(variant["id"], variant["user_input"])
        original_correct = _coordinator_correct(original, original_actual)
        variant_correct = _coordinator_correct(variant, variant_actual)
        pairs.append(
            {
                "original_id": original["id"],
                "variant_id": variant["id"],
                "original_user_input": original["user_input"],
                "variant_user_input": variant["user_input"],
                "original_keywords": variant["original_keywords"],
                "original_correct": original_correct,
                "variant_correct": variant_correct,
                "drop": int(original_correct) - int(variant_correct),
                "original_actual": original_actual,
                "variant_actual": variant_actual,
            }
        )

    original_accuracy = sum(1 for item in pairs if item["original_correct"]) / len(pairs)
    variant_accuracy = sum(1 for item in pairs if item["variant_correct"]) / len(pairs)
    drop = original_accuracy - variant_accuracy
    if drop <= 0.15:
        conclusion = "LLM 真懂语义"
        judgment = "✅"
    elif drop <= 0.30:
        conclusion = "灰色"
        judgment = "⚠️"
    else:
        conclusion = "也只在抓关键词"
        judgment = "❌"
    return {
        "test": "F",
        "scenario_count": len(variants),
        "original_accuracy": original_accuracy,
        "variant_accuracy": variant_accuracy,
        "drop": drop,
        "conclusion": conclusion,
        "judgment": judgment,
        "pairs": pairs,
    }


def _coordinator_correct(scenario: dict[str, Any], actual: dict[str, Any]) -> bool:
    if actual.get("tool") != scenario["expected_coordinator_tool"]:
        return False
    if scenario["expected_coordinator_tool"] != "remember":
        return True
    return (
        actual.get("kind") == scenario["expected_kind"]
        and domain_matches(scenario["expected_domain"], actual.get("domain"))
    )
