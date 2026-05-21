"""Run round 2 coordinator benchmark and generate REPORT.md."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round2.explicit_mode import run_explicit_mode_test
from experiments.benchmarks.round2.near_synonym_test import run_near_synonym_test
from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2, validate_scenarios_v2
from experiments.benchmarks.round2.synonym_test import run_synonym_test


ROUND2_DIR = Path(__file__).resolve().parent


def run_round2(output_dir: str | Path | None = None) -> dict[str, Any]:
    validate_scenarios_v2(SCENARIOS_V2)
    target_dir = Path(output_dir) if output_dir else ROUND2_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    result_d = run_explicit_mode_test(SCENARIOS_V2)
    result_c = run_synonym_test(SCENARIOS_V2)
    result_e = run_near_synonym_test(SCENARIOS_V2)

    _write_json(target_dir / "results_d.json", result_d)
    _write_json(target_dir / "results_c.json", result_c)
    _write_json(target_dir / "results_e.json", result_e)

    report = render_report(result_d, result_c, result_e)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")

    return {
        "scenario_count": len(SCENARIOS_V2),
        "result_d": result_d,
        "result_c": result_c,
        "result_e": result_e,
        "report_path": str(target_dir / "REPORT.md"),
    }


def render_report(result_d: dict[str, Any], result_c: dict[str, Any], result_e: dict[str, Any]) -> str:
    recommendation = _recommendation(result_d, result_c, result_e)
    lines = [
        "# MCP 协调器第二轮验证基准报告",
        "",
        "## 1. 运行说明",
        "",
        "- 评估代理：rule_based",
        "- 真实 LLM：本轮按任务要求不使用",
        "- 总场景数：50",
        "- 测试 D：30 个第一轮复用场景中的 13 个 remember 场景",
        "- 测试 C：10 个同义改写场景（2 组×5）",
        "- 测试 E：10 个近义工具混淆场景",
        "- 边界：未修改 `mcp_server.py` / `core.py` / `setup_wizard.py` / `experiments/coordinator/high_level_actions.py`",
        "",
        "## 2. 测试 D 结果",
        "",
        "| 指标 | 关键词推断（第一轮原型） | 显式传参（第二轮） | 差异 |",
        "|------|-------------------------|--------------------|------|",
        _metric_row(
            "Easy kind",
            result_d["keyword"]["easy_kind_accuracy"],
            result_d["explicit"]["easy_kind_accuracy"],
        ),
        _metric_row(
            "Medium kind",
            result_d["keyword"]["medium_kind_accuracy"],
            result_d["explicit"]["medium_kind_accuracy"],
        ),
        _metric_row(
            "Hard kind",
            result_d["keyword"]["hard_kind_accuracy"],
            result_d["explicit"]["hard_kind_accuracy"],
        ),
        _metric_row(
            "整体 domain",
            result_d["keyword"]["domain_accuracy"],
            result_d["explicit"]["domain_accuracy"],
        ),
        "",
        f"判定结论：{'通过' if result_d['pass'] else '不通过'}。",
        "通过门槛：Hard kind ≥ 95%，整体 domain ≥ 90%，Easy/Medium kind ≥ 98%。",
        f"样本说明：Easy={result_d['explicit']['counts_by_difficulty']['easy']}，"
        f"Medium={result_d['explicit']['counts_by_difficulty']['medium']}，"
        f"Hard={result_d['explicit']['counts_by_difficulty']['hard']}；Medium 为空时表格记为 N/A。",
        "",
        "## 3. 测试 C 结果",
        "",
        "| 同义改写组 | 关键词模式稳定性 | 显式传参稳定性 |",
        "|-----------|-----------------|----------------|",
    ]

    for group in result_c["groups"]:
        lines.append(
            f"| {group['group']} | {group['keyword_stable_count']}/{group['size']} "
            f"({_pct(group['keyword_stability'])}) | {group['explicit_stable_count']}/{group['size']} "
            f"({_pct(group['explicit_stability'])}) |"
        )

    lines.extend(
        [
            "",
            f"- 关键词模式整体稳定性：{_pct(result_c['keyword_overall_stability'])}",
            f"- 显式传参整体稳定性：{_pct(result_c['explicit_overall_stability'])}",
            f"- 差距：{_signed_pct(result_c['delta'])}",
            f"- 判定结论：{'通过' if result_c['pass'] else '不通过'}。",
            "",
            "## 4. 测试 E 结果",
            "",
            "| 近义工具组 | 37 工具准确率 |",
            "|-----------|--------------|",
        ]
    )

    for group in result_e["groups"]:
        lines.append(
            f"| {group['group']} | {group['correct']}/{group['total']} ({_pct(group['accuracy'])}) |"
        )

    lines.extend(
        [
            "",
            f"- 37 工具总体准确率：{result_e['correct']}/{result_e['scenario_count']} ({_pct(result_e['accuracy'])})",
            f"- 判定结论：{result_e['decision']}。",
            "",
            "代表性错例：",
            "",
        ]
    )
    for item in [item for item in result_e["results"] if not item["correct"]][:5]:
        lines.extend(
            [
                f"- {item['id']}：期望 `{item['expected']}`，实际 `{item['actual']}`；{item['user_input']}",
            ]
        )

    lines.extend(
        [
            "",
            "## 5. 综合决策",
            "",
            "### Q1：显式传参方案是否能上线？",
            "",
            (
                "可以作为下一版协调器的必要条件。"
                if result_d["pass"]
                else "不能上线；D 未通过时应放弃协调器路线。"
            ),
            f"数据：Hard kind 从 {_pct(result_d['keyword']['hard_kind_accuracy'])} 到 "
            f"{_pct(result_d['explicit']['hard_kind_accuracy'])}，domain 从 "
            f"{_pct(result_d['keyword']['domain_accuracy'])} 到 {_pct(result_d['explicit']['domain_accuracy'])}。",
            "",
            "### Q2：协调器是否对同义改写更稳定？",
            "",
            (
                "显式传参明显更稳定。"
                if result_c["pass"]
                else "没有证明显式传参足够稳定。"
            ),
            f"数据：关键词稳定性 {_pct(result_c['keyword_overall_stability'])}，显式传参 "
            f"{_pct(result_c['explicit_overall_stability'])}，差距 {_signed_pct(result_c['delta'])}。",
            "",
            "### Q3：37 工具在近义场景下是否真的够用？",
            "",
            f"测试 E 给出的结论是：{result_e['decision']}。"
            f"37 工具在近义混淆场景下准确率为 {_pct(result_e['accuracy'])}。",
            "",
            "### 最终建议",
            "",
            recommendation,
            "",
            "## 6. 第三轮测试预告",
            "",
            "- 真实 LLM：需要 OpenAI/Anthropic API key，每个场景至少 3 次取多数。",
            "- 端到端写入：调用 Engram 后检查 lesson/decision 内容、domain、reasoning 是否符合预期。",
            "- 大样本：扩到 100+ 场景，覆盖更多自然语言、项目上下文和维护动作。",
            "",
        ]
    )

    return "\n".join(lines)


def _recommendation(result_d: dict[str, Any], result_c: dict[str, Any], result_e: dict[str, Any]) -> str:
    if not result_d["pass"]:
        return "方案 A：不做协调器。D 未通过，显式传参也无法修复核心分类问题。"
    if result_d["pass"] and result_c["pass"] and result_e["decision"] == "协调器有价值":
        return (
            "方案 B：做协调器 + 显式传参，但先进入第三轮真实 LLM 和端到端测试。"
            "本轮 D/C 通过，E 显示 37 工具在近义场景下不足。"
        )
    return "方案 C：继续迭代不上线。本轮存在灰色地带，需要真实 LLM 复测后再定。"


def _metric_row(label: str, keyword: float, explicit: float) -> str:
    if keyword is None or explicit is None:
        return f"| {label} | {_pct_or_na(keyword)} | {_pct_or_na(explicit)} | N/A |"
    return f"| {label} | {_pct(keyword)} | {_pct(explicit)} | {_signed_pct(explicit - keyword)} |"


def _pct_or_na(value: float | None) -> str:
    if value is None:
        return "N/A"
    return _pct(value)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}pp"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    result = run_round2()
    d = result["result_d"]
    c = result["result_c"]
    e = result["result_e"]
    print(f"Test D: {'PASS' if d['pass'] else 'FAIL'}")
    print(f"Test C: {'PASS' if c['pass'] else 'FAIL'}")
    print(f"Test E: {e['decision']} ({_pct(e['accuracy'])})")
    print(f"Report: {result['report_path']}")


if __name__ == "__main__":
    main()
