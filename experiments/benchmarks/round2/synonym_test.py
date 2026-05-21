"""Synonym rewrite robustness test."""

from __future__ import annotations

from collections import Counter
from typing import Any

from experiments.benchmarks.round2.explicit_mode import judge_kind_domain
from experiments.coordinator.high_level_actions import infer_domain, looks_like_decision


def run_synonym_test(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    synonym_scenarios = [scenario for scenario in scenarios if scenario["test_group"] == "C"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scenario in synonym_scenarios:
        grouped.setdefault(scenario["synonym_group"], []).append(scenario)

    groups = []
    for name, items in sorted(grouped.items()):
        keyword_outputs = [_keyword_tuple(item["user_input"]) for item in items]
        explicit_outputs = [judge_kind_domain(item["user_input"]) for item in items]
        groups.append(
            {
                "group": name,
                "size": len(items),
                "keyword_outputs": [_format_tuple(value) for value in keyword_outputs],
                "explicit_outputs": [_format_tuple(value) for value in explicit_outputs],
                "keyword_stable_count": _stable_count(keyword_outputs),
                "explicit_stable_count": _stable_count(explicit_outputs),
                "keyword_stability": _stable_count(keyword_outputs) / len(items),
                "explicit_stability": _stable_count(explicit_outputs) / len(items),
            }
        )

    keyword_overall = sum(group["keyword_stable_count"] for group in groups) / sum(
        group["size"] for group in groups
    )
    explicit_overall = sum(group["explicit_stable_count"] for group in groups) / sum(
        group["size"] for group in groups
    )
    pass_status = (
        explicit_overall >= 0.80
        and explicit_overall - keyword_overall >= 0.15
    )

    return {
        "test": "C",
        "scenario_count": len(synonym_scenarios),
        "group_count": len(groups),
        "groups": groups,
        "keyword_overall_stability": keyword_overall,
        "explicit_overall_stability": explicit_overall,
        "delta": explicit_overall - keyword_overall,
        "pass": pass_status,
        "thresholds": {
            "explicit_overall_stability": 0.80,
            "delta": 0.15,
        },
    }


def _keyword_tuple(content: str) -> tuple[str, str]:
    kind = "decision" if looks_like_decision(content) else "lesson"
    return kind, infer_domain(content)


def _stable_count(values: list[tuple[str, str]]) -> int:
    return Counter(values).most_common(1)[0][1] if values else 0


def _format_tuple(value: tuple[str, str]) -> str:
    return f"{value[0]}/{value[1]}"
