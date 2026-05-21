"""Run the MCP tool-selection benchmark and generate the decision report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from experiments.benchmarks.llm_judge import (
    get_llm_availability,
    load_atomic_tool_descriptions,
    load_coordinator_tool_descriptions,
)
from experiments.benchmarks.rule_based_judge import choose_atomic_tool, choose_coordinator_tool
from experiments.benchmarks.scenarios import SCENARIOS, validate_scenarios
from experiments.coordinator.high_level_actions import infer_domain, looks_like_decision


BENCHMARK_DIR = Path(__file__).resolve().parent


def evaluate_tool_selection(
    scenarios: list[dict[str, Any]],
    mode: str,
    chooser: Callable[[str], str],
) -> dict[str, Any]:
    """Evaluate atomic or coordinator tool selection."""
    expected_key = "expected_atomic_tool" if mode == "atomic" else "expected_coordinator_tool"
    results = []
    for scenario in scenarios:
        actual = chooser(scenario["user_input"])
        expected = scenario[expected_key]
        results.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "difficulty": scenario["difficulty"],
                "user_input": scenario["user_input"],
                "expected": expected,
                "actual": actual,
                "correct": actual == expected,
            }
        )

    return {
        "mode": mode,
        "results": results,
        "summary": summarize_results(results),
    }


def evaluate_internal_classifier(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the original coordinator kind/domain heuristics."""
    remember_scenarios = [
        scenario
        for scenario in scenarios
        if scenario["expected_coordinator_tool"] == "remember"
    ]

    kind_results = []
    domain_results = []
    for scenario in remember_scenarios:
        content = scenario["user_input"]
        actual_kind = "decision" if looks_like_decision(content) else "lesson"
        actual_domain = infer_domain(content)

        kind_results.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "difficulty": scenario["difficulty"],
                "user_input": content,
                "expected": scenario["expected_kind"],
                "actual": actual_kind,
                "correct": actual_kind == scenario["expected_kind"],
            }
        )
        domain_results.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "difficulty": scenario["difficulty"],
                "user_input": content,
                "expected": scenario["expected_domain"],
                "actual": actual_domain,
                "correct": actual_domain == scenario["expected_domain"],
            }
        )

    hard_kind_results = [item for item in kind_results if item["difficulty"] == "hard"]
    return {
        "scenario_count": len(remember_scenarios),
        "kind_accuracy": accuracy(kind_results),
        "domain_accuracy": accuracy(domain_results),
        "hard_kind_error_rate": 1.0 - accuracy(hard_kind_results),
        "kind_results": kind_results,
        "domain_results": domain_results,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize accuracy overall and by benchmark group."""
    summary = {
        "total": len(results),
        "correct": sum(1 for item in results if item["correct"]),
        "accuracy": accuracy(results),
        "by_difficulty": {},
        "by_category": {},
        "errors": [item for item in results if not item["correct"]],
    }
    for key in ("difficulty", "category"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            groups.setdefault(item[key], []).append(item)
        target = "by_difficulty" if key == "difficulty" else "by_category"
        summary[target] = {
            name: {
                "total": len(items),
                "correct": sum(1 for item in items if item["correct"]),
                "accuracy": accuracy(items),
            }
            for name, items in sorted(groups.items())
        }
    return summary


def accuracy(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if item["correct"]) / len(items)


def run_benchmark(output_dir: str | Path | None = None) -> dict[str, Any]:
    """Run all benchmark groups and write JSON results plus REPORT.md."""
    validate_scenarios(SCENARIOS)
    target_dir = Path(output_dir) if output_dir else BENCHMARK_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    llm_availability = get_llm_availability()
    judge_info = {
        "judge": "rule_based",
        "llm_available": llm_availability["available"],
        "llm_reasons": llm_availability["reasons"],
        "note": (
            "真实 LLM API 当前不可用，使用透明规则代理。"
            "该结果不是 Claude/Cursor 的实测行为。"
        ),
    }

    atomic = evaluate_tool_selection(SCENARIOS, "atomic", choose_atomic_tool)
    coordinator = evaluate_tool_selection(SCENARIOS, "coordinator", choose_coordinator_tool)
    internal = evaluate_internal_classifier(SCENARIOS)

    atomic["tool_count"] = len(load_atomic_tool_descriptions())
    coordinator["tool_count"] = len(load_coordinator_tool_descriptions())
    coordinator["fallback_note"] = (
        "advanced 场景的期望行为按任务包记为 fallback/router；"
        "如果强制只能从 5 个协调器工具中选择，这些场景无法被正确覆盖。"
    )

    _write_json(target_dir / "results_atomic.json", atomic)
    _write_json(target_dir / "results_coordinator.json", coordinator)
    _write_json(target_dir / "results_internal.json", internal)

    report = render_report(atomic, coordinator, internal, judge_info)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")

    return {
        "judge_info": judge_info,
        "atomic": atomic,
        "coordinator": coordinator,
        "internal": internal,
        "report_path": str(target_dir / "REPORT.md"),
    }


def render_report(
    atomic: dict[str, Any],
    coordinator: dict[str, Any],
    internal: dict[str, Any],
    judge_info: dict[str, Any],
) -> str:
    """Render the benchmark decision report in Chinese."""
    atomic_accuracy = _summary_accuracy(atomic)
    coordinator_accuracy = _summary_accuracy(coordinator)
    accuracy_delta = coordinator_accuracy - atomic_accuracy
    if accuracy_delta >= 0.10:
        q1_conclusion = (
            f"在本次规则代理数据里，协调器/回退准确率为 {_pct(coordinator_accuracy)}，"
            f"比 37 工具的 {_pct(atomic_accuracy)} 高 {_signed_pct(accuracy_delta)}，达到 10 个百分点阈值。"
            "但由于这不是真实 LLM 实测，仍需要 API 复跑确认。"
        )
    elif accuracy_delta < 0:
        q1_conclusion = (
            f"在本次规则代理数据里，协调器/回退准确率为 {_pct(coordinator_accuracy)}，"
            f"低于 37 工具的 {_pct(atomic_accuracy)}（{_signed_pct(accuracy_delta)}）。"
            "因此当前数据不支持“协调器提升选择准确率”的结论。"
        )
    else:
        q1_conclusion = (
            f"在本次规则代理数据里，37 工具与 5 协调器/回退的工具选择准确率相同，"
            f"都是 {_pct(atomic_accuracy)}。因此没有数据证明协调器单靠“减少工具数量”能带来显著提升；"
            "在没有真实 LLM 实测前，不能把协调器收益说成已被证明。"
        )

    lines = [
        "# MCP 工具选择准确率基准测试报告",
        "",
        "## 运行说明",
        "",
        f"- 评估代理：{judge_info['judge']}",
        f"- 真实 LLM 可用：{judge_info['llm_available']}",
        f"- LLM 不可用原因：{'; '.join(judge_info['llm_reasons']) or '无'}",
        f"- 说明：{judge_info['note']}",
        f"- 原子工具数：{atomic['tool_count']}",
        f"- 协调器工具数：{coordinator['tool_count']}（advanced 正确行为单独记为 fallback/router）",
        "",
        "## 1. 数据对比表",
        "",
        "| 指标 | 37 工具 | 5 协调器/回退 | 差异 |",
        "|------|--------|---------------|------|",
    ]

    rows = [
        ("总体准确率", _summary_accuracy(atomic), _summary_accuracy(coordinator)),
        ("Easy 场景", _difficulty_accuracy(atomic, "easy"), _difficulty_accuracy(coordinator, "easy")),
        ("Medium 场景", _difficulty_accuracy(atomic, "medium"), _difficulty_accuracy(coordinator, "medium")),
        ("Hard 场景", _difficulty_accuracy(atomic, "hard"), _difficulty_accuracy(coordinator, "hard")),
        ("Advanced 场景", _category_accuracy(atomic, "advanced"), _category_accuracy(coordinator, "advanced")),
    ]
    for label, atomic_value, coordinator_value in rows:
        lines.append(
            f"| {label} | {_pct(atomic_value)} | {_pct(coordinator_value)} | "
            f"{_signed_pct(coordinator_value - atomic_value)} |"
        )

    lines.extend(
        [
            "",
            "补充：这里把 advanced 的正确处理记为 fallback/router，因为任务包明确要求观察 5 个协调器覆盖不到的场景。"
            "如果产品形态强制只暴露 `remember/recall/cleanup/inherit/sync` 且没有回退层，advanced 场景准确率应视为 0%。",
            "",
            "## 2. 协调器内部判断准确率",
            "",
            "| 判断 | 准确率 | 主要错误模式 |",
            "|------|--------|------------|",
            f"| lesson vs decision | {_pct(internal['kind_accuracy'])} | {_kind_error_pattern(internal)} |",
            f"| domain 推断 | {_pct(internal['domain_accuracy'])} | {_domain_error_pattern(internal)} |",
            "",
            f"- Hard 场景下 kind 误判率：{_pct(internal['hard_kind_error_rate'])}",
            f"- remember 类场景数量：{internal['scenario_count']}",
            "",
            "## 3. 错误案例分析",
            "",
        ]
    )

    for case in representative_errors(atomic, coordinator, internal)[:7]:
        lines.extend(
            [
                f"### {case['id']}：{case['type']}",
                "",
                f"- 场景内容：{case['user_input']}",
                f"- 期望：{case['expected']}",
                f"- 实际：{case['actual']}",
                f"- 错误原因分析：{case['reason']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 4. 结论与建议",
            "",
            "### Q1：协调器是否真的提升了 AI 选择准确率？",
            "",
            q1_conclusion,
            "",
            "### Q2：协调器的内部判断是否够用？",
            "",
            f"不够。当前关键词分类的 kind 准确率只有 {_pct(internal['kind_accuracy'])}，"
            f"domain 准确率只有 {_pct(internal['domain_accuracy'])}，kind 误判率已经高于 15% 阈值。"
            "尤其是含“决定/选择”但本质是 lesson 的陷阱，以及没有关键词但本质是 decision 的表达，会稳定误判。",
            "",
            "### Q3：advanced 场景如何处理？",
            "",
            "必须保留 advanced tier 或显式 fallback/router。导出、审计日志、手动合并、身份配置更新都不是五个高层工具能自然承载的动作；"
            "如果强行塞进 `cleanup` 或 `recall`，会把 schema 清晰度重新变成隐式路由问题。",
            "",
            "### Q4：综合建议",
            "",
            "推荐方案 C 的收缩版：可以继续探索协调器，但不能只做 5 个工具。更稳妥的产品形态是：",
            "",
            "- 默认层保留 `remember/recall/cleanup/inherit/sync`，降低普通任务的选择负担。",
            "- `remember` 必须让 AI 显式传 `kind` 和 `domain`，不要依赖当前关键词推断。",
            "- 保留 advanced tier/fallback，覆盖 export、audit、merge、identity update 等维护动作。",
            "- 在真实 LLM API 可用后复跑本基准，每个场景 3 次取多数，再决定是否替代现有 37 工具。",
            "",
            "当前数据不支持直接替换为纯 5 工具的强结论；支持的结论是：协调器可以继续作为默认使用层探索，但原型内部判断必须重做，advanced 不能消失。",
            "",
        ]
    )

    return "\n".join(lines)


def representative_errors(
    atomic: dict[str, Any],
    coordinator: dict[str, Any],
    internal: dict[str, Any],
) -> list[dict[str, str]]:
    """Collect representative tool-selection and internal-classifier mistakes."""
    cases: list[dict[str, str]] = []

    for source_name, source in (("37 工具错选", atomic), ("协调器错选", coordinator)):
        for item in source["summary"]["errors"]:
            cases.append(
                {
                    "id": item["id"],
                    "type": source_name,
                    "user_input": item["user_input"],
                    "expected": item["expected"],
                    "actual": item["actual"],
                    "reason": "规则评估代理根据表层意图选到了不同工具。",
                }
            )

    for item in internal["kind_results"]:
        if item["correct"]:
            continue
        cases.append(
            {
                "id": item["id"],
                "type": "内部 kind 误判",
                "user_input": item["user_input"],
                "expected": f"kind={item['expected']}",
                "actual": f"kind={item['actual']}",
                "reason": "原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。",
            }
        )

    for item in internal["domain_results"]:
        if item["correct"]:
            continue
        cases.append(
            {
                "id": item["id"],
                "type": "内部 domain 误判",
                "user_input": item["user_input"],
                "expected": f"domain={item['expected']}",
                "actual": f"domain={item['actual']}",
                "reason": "infer_domain 目前只识别 mcp/tool/工具 与 project/asset/路径/图谱，覆盖面过窄。",
            }
        )

    return cases


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary_accuracy(result: dict[str, Any]) -> float:
    return result["summary"]["accuracy"]


def _difficulty_accuracy(result: dict[str, Any], difficulty: str) -> float:
    return result["summary"]["by_difficulty"].get(difficulty, {}).get("accuracy", 0.0)


def _category_accuracy(result: dict[str, Any], category: str) -> float:
    return result["summary"]["by_category"].get(category, {}).get("accuracy", 0.0)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}pp"


def _kind_error_pattern(internal: dict[str, Any]) -> str:
    errors = [item for item in internal["kind_results"] if not item["correct"]]
    if not errors:
        return "无"
    lesson_as_decision = sum(
        1 for item in errors if item["expected"] == "lesson" and item["actual"] == "decision"
    )
    decision_as_lesson = sum(
        1 for item in errors if item["expected"] == "decision" and item["actual"] == "lesson"
    )
    return f"lesson 被关键词误判为 decision：{lesson_as_decision}；无关键词 decision 被判 lesson：{decision_as_lesson}"


def _domain_error_pattern(internal: dict[str, Any]) -> str:
    errors = [item for item in internal["domain_results"] if not item["correct"]]
    if not errors:
        return "无"
    actual_counts: dict[str, int] = {}
    for item in errors:
        actual_counts[item["actual"]] = actual_counts.get(item["actual"], 0) + 1
    parts = [f"{domain}={count}" for domain, count in sorted(actual_counts.items())]
    return "误判输出集中在 " + "，".join(parts)


def main() -> None:
    result = run_benchmark()
    atomic = result["atomic"]["summary"]["accuracy"]
    coordinator = result["coordinator"]["summary"]["accuracy"]
    internal = result["internal"]
    print(f"Atomic accuracy: {_pct(atomic)}")
    print(f"Coordinator accuracy: {_pct(coordinator)}")
    print(f"Internal kind accuracy: {_pct(internal['kind_accuracy'])}")
    print(f"Internal domain accuracy: {_pct(internal['domain_accuracy'])}")
    print(f"Report: {result['report_path']}")


if __name__ == "__main__":
    main()
