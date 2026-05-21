"""Test C: synonym rewrite robustness with real LLM coordinator judgments."""

from __future__ import annotations

from collections import Counter
from typing import Any


def run_test_c(scenarios: list[dict[str, Any]], judge: Any) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for scenario in scenarios:
        group = scenario.get("synonym_group")
        if group:
            grouped.setdefault(group, []).append(scenario)

    groups = []
    for name, items in sorted(grouped.items()):
        outputs = []
        for scenario in items:
            actual = judge.judge_coordinator(scenario["id"], scenario["user_input"])
            outputs.append(
                {
                    "id": scenario["id"],
                    "tool": actual.get("tool"),
                    "kind": actual.get("kind"),
                    "domain": actual.get("domain"),
                    "reasoning": actual.get("reasoning", ""),
                }
            )
        tool_counts = Counter(item["tool"] for item in outputs)
        stable_tool, stable_count = tool_counts.most_common(1)[0]
        ratio = stable_count / len(items)
        groups.append(
            {
                "group": name,
                "topic": name,
                "total": len(items),
                "stable_tool": stable_tool,
                "stable_count": stable_count,
                "stability": ratio,
                "judgment": _judge_threshold(ratio, 0.80, 0.60),
                "outputs": outputs,
            }
        )

    average = sum(group["stability"] for group in groups) / len(groups) if groups else 0.0
    return {
        "test": "C",
        "group_count": len(groups),
        "scenario_count": sum(group["total"] for group in groups),
        "average_stability": average,
        "judgment": _judge_threshold(average, 0.80, 0.60),
        "groups": groups,
    }


def _judge_threshold(value: float, pass_at: float, warn_at: float) -> str:
    if value >= pass_at:
        return "✅"
    if value >= warn_at:
        return "⚠️"
    return "❌"
