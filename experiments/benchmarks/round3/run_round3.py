"""Run round 3 LLM benchmark and write the product-decision report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round3.llm_judge import LLMJudge
from experiments.benchmarks.round3.scenarios_v3 import SCENARIOS_V3, validate_scenarios_v3
from experiments.benchmarks.round3.test_c_synonym import run_test_c
from experiments.benchmarks.round3.test_d_explicit import run_test_d
from experiments.benchmarks.round3.test_e_near_synonym import run_test_e
from experiments.benchmarks.round3.test_f_keyword_leak import run_test_f


ROUND3_DIR = Path(__file__).resolve().parent


def run_round3(output_dir: str | Path | None = None, judge: Any | None = None) -> dict[str, Any]:
    validate_scenarios_v3(SCENARIOS_V3)
    target_dir = Path(output_dir) if output_dir else ROUND3_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if judge is None:
        raw_path = target_dir / "results_raw.jsonl"
        if raw_path.exists():
            raw_path.unlink()
        judge = LLMJudge(raw_log_path=raw_path, runs_per_scenario=3)

    result_d = run_test_d(SCENARIOS_V3, judge)
    result_c = run_test_c(SCENARIOS_V3, judge)
    result_e = run_test_e(SCENARIOS_V3, judge)
    result_f = run_test_f(SCENARIOS_V3, judge)

    _write_json(target_dir / "results_d.json", result_d)
    _write_json(target_dir / "results_c.json", result_c)
    _write_json(target_dir / "results_e.json", result_e)
    _write_json(target_dir / "results_f.json", result_f)

    judge_info = {
        "model": getattr(getattr(judge, "client", None), "model", "deepseek-chat"),
        "temperature": 0.0,
        "runs_per_scenario": getattr(judge, "runs_per_scenario", 3),
        "total_calls": getattr(judge, "total_calls", 0),
        "failed_calls": getattr(judge, "failed_calls", 0),
        "usage": getattr(judge, "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    }
    report = render_report(result_d, result_c, result_e, result_f, judge_info)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")

    return {
        "scenario_count": len(SCENARIOS_V3),
        "result_d": result_d,
        "result_c": result_c,
        "result_e": result_e,
        "result_f": result_f,
        "judge_info": judge_info,
        "report_path": str(target_dir / "REPORT.md"),
    }


def render_report(
    result_d: dict[str, Any],
    result_c: dict[str, Any],
    result_e: dict[str, Any],
    result_f: dict[str, Any],
    judge_info: dict[str, Any],
) -> str:
    recommendation = _final_recommendation(result_d, result_c, result_e, result_f)
    failed_calls = judge_info["failed_calls"]
    total_calls = judge_info["total_calls"]
    failure_rate = failed_calls / total_calls if total_calls else 0.0
    usage = judge_info["usage"]

    lines = [
        "# MCP 协调器第三轮验证基准报告",
        "",
        "## 1. 运行说明",
        "",
        f"- LLM：DeepSeek V3（`{judge_info['model']}`）",
        f"- 温度：{judge_info['temperature']}",
        f"- 每场景次数：{judge_info['runs_per_scenario']} 次取多数",
        f"- 总调用次数：{total_calls}",
        f"- 失败调用：{failed_calls}（{_pct(failure_rate)}）",
        f"- Usage：prompt={usage.get('prompt_tokens', 0)}，completion={usage.get('completion_tokens', 0)}，total={usage.get('total_tokens', 0)} tokens",
        "- 成本：本报告只记录 API usage，未按实时价格换算人民币；实际费用以 DeepSeek 控制台为准。",
        "",
        "## 2. 测试 D 结果（真实 AI 显式传参）",
        "",
        "| 指标 | 准确率 | 判定 |",
        "|------|--------|------|",
        f"| Easy kind | {_pct_or_na(result_d['summary']['easy_kind'])} | {result_d['judgments']['easy_kind']} |",
        f"| Medium kind | {_pct_or_na(result_d['summary']['medium_kind'])} | {result_d['judgments']['medium_kind']} |",
        f"| Hard kind | {_pct_or_na(result_d['summary']['hard_kind'])} | {result_d['judgments']['hard_kind']} |",
        f"| 整体 domain（含同义） | {_pct(result_d['summary']['domain'])} | {result_d['judgments']['domain']} |",
        "",
        "Hard 场景逐条结果：",
        "",
        "| id | user_input | expected | actual | 正确 |",
        "|----|------------|----------|--------|------|",
    ]

    for item in result_d["hard_results"]:
        expected = f"{item['expected_tool']}/{item['expected_kind']}/{item['expected_domain']}"
        actual = f"{item['actual_tool']}/{item['actual_kind']}/{item['actual_domain']}"
        correct = "是" if item["kind_correct"] and item["domain_correct"] else "否"
        lines.append(f"| {item['id']} | {item['user_input']} | {expected} | {actual} | {correct} |")

    lines.extend(
        [
            "",
            "## 3. 测试 C 结果（同义改写）",
            "",
            "| 组 | 主题 | 同工具比例 | 判定 |",
            "|----|------|-----------|------|",
        ]
    )
    for group in result_c["groups"]:
        lines.append(
            f"| {group['group']} | {group['topic']} | {group['stable_count']}/{group['total']} ({_pct(group['stability'])}) | {group['judgment']} |"
        )
    lines.append(
        f"| 平均 | | {_pct(result_c['average_stability'])} | {result_c['judgment']} |"
    )

    lines.extend(
        [
            "",
            "## 4. 测试 E 结果（37 工具近义）",
            "",
            "| 组 | 准确率 |",
            "|----|--------|",
        ]
    )
    for group in result_e["groups"]:
        lines.append(f"| {group['group']} | {group['correct']}/{group['total']} ({_pct(group['accuracy'])}) |")
    lines.extend(
        [
            f"| 总体 | {result_e['correct']}/{result_e['scenario_count']} ({_pct(result_e['accuracy'])}) |",
            "",
            f"判定：{result_e['decision']} {result_e['judgment']}",
            "",
            "## 5. 测试 F 结果（关键词泄露检测）",
            "",
            "| 原场景 | 原版准确率 | 变体准确率 | 下降 |",
            "|--------|----------|----------|------|",
        ]
    )
    for pair in result_f["pairs"]:
        lines.append(
            f"| {pair['original_id']} → {pair['variant_id']} | {_bool_mark(pair['original_correct'])} | {_bool_mark(pair['variant_correct'])} | {_signed_pct(pair['drop'])} |"
        )
    lines.extend(
        [
            f"| 平均 | {_pct(result_f['original_accuracy'])} | {_pct(result_f['variant_accuracy'])} | {_signed_pct(result_f['drop'])} |",
            "",
            f"判定：{result_f['conclusion']} {result_f['judgment']}",
            "",
            "## 6. 与第二轮对比",
            "",
            "| 测试 | 第二轮（rule） | 第三轮（DeepSeek） | 真实差距 |",
            "|------|---------------|--------------------|---------|",
            f"| D hard kind | 100.0%（过拟合） | {_pct_or_na(result_d['summary']['hard_kind'])} | {_diff_from_round2(result_d['summary']['hard_kind'], 1.0)} |",
            f"| D domain | 100.0%（过拟合） | {_pct(result_d['summary']['domain'])} | {_signed_pct(result_d['summary']['domain'] - 1.0)} |",
            f"| C 同义稳定性 | 100.0%（过拟合） | {_pct(result_c['average_stability'])} | {_signed_pct(result_c['average_stability'] - 1.0)} |",
            f"| E 37 工具 | 40.0% | {_pct(result_e['accuracy'])} | {_signed_pct(result_e['accuracy'] - 0.40)} |",
            "",
            "## 7. 综合决策",
            "",
            "### Q1：协调器 + AI 显式传参方案是否能上线？",
            "",
            _q1(result_d),
            "",
            "### Q2：协调器是否真的对同义改写更稳定？",
            "",
            _q2(result_c),
            "",
            "### Q3：37 工具是否真的不够用？",
            "",
            _q3(result_e),
            "",
            "### Q4：AI 是真懂语义还是抓关键词？",
            "",
            _q4(result_f),
            "",
            "### 最终建议",
            "",
            recommendation,
            "",
            "## 8. 局限说明",
            "",
            "- DeepSeek V3 不是 Claude/Codex 本体，结论可能不完全代表实际使用环境。",
            "- 65 个场景仍是有限样本。",
            f"- 本轮失败调用率为 {_pct(failure_rate)}；若失败率升高，需要复跑。",
            "- 若进入实现，应再做端到端写入验证，检查实际存储内容是否符合预期。",
            "",
        ]
    )
    return "\n".join(lines)


def _final_recommendation(
    result_d: dict[str, Any],
    result_c: dict[str, Any],
    result_e: dict[str, Any],
    result_f: dict[str, Any],
) -> str:
    if "❌" in (result_d["judgments"]["hard_kind"], result_d["judgments"]["domain"], result_f["judgment"]):
        failed = []
        if "❌" in (result_d["judgments"]["hard_kind"], result_d["judgments"]["domain"]):
            failed.append("D")
        if result_f["judgment"] == "❌":
            failed.append("F")
        return f"方案 C：放弃协调器，保持 37 工具。{'/'.join(failed)} 不通过，说明显式传参或语义鲁棒性不足。"
    if "⚠️" in (
        result_d["judgments"]["hard_kind"],
        result_d["judgments"]["domain"],
        result_c["judgment"],
        result_e["judgment"],
        result_f["judgment"],
    ):
        return "方案 B：协调器方向继续，但需要更多迭代。存在灰色指标，暂不直接上线。"
    return "方案 A：上线协调器（显式传参版）。D/C/E/F 均通过，数据支持继续产品化。"


def _q1(result_d: dict[str, Any]) -> str:
    return (
        f"Hard kind={_pct_or_na(result_d['summary']['hard_kind'])}，"
        f"domain={_pct(result_d['summary']['domain'])}。"
        f"判定：hard kind {result_d['judgments']['hard_kind']}，domain {result_d['judgments']['domain']}。"
    )


def _q2(result_c: dict[str, Any]) -> str:
    return f"5 组平均同工具稳定性为 {_pct(result_c['average_stability'])}，判定 {result_c['judgment']}。"


def _q3(result_e: dict[str, Any]) -> str:
    return f"37 工具近义场景准确率 {_pct(result_e['accuracy'])}，判定：{result_e['decision']} {result_e['judgment']}。"


def _q4(result_f: dict[str, Any]) -> str:
    return (
        f"关键词替换后准确率从 {_pct(result_f['original_accuracy'])} 到 {_pct(result_f['variant_accuracy'])}，"
        f"下降 {_signed_pct(result_f['drop'])}，判定：{result_f['conclusion']} {result_f['judgment']}。"
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _pct_or_na(value: float | None) -> str:
    if value is None:
        return "N/A"
    return _pct(value)


def _signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}pp"


def _diff_from_round2(value: float | None, baseline: float) -> str:
    if value is None:
        return "N/A"
    return _signed_pct(value - baseline)


def _bool_mark(value: bool) -> str:
    return "✅" if value else "❌"


def main() -> None:
    result = run_round3()
    print(f"D hard kind: {_pct_or_na(result['result_d']['summary']['hard_kind'])}")
    print(f"D domain: {_pct(result['result_d']['summary']['domain'])}")
    print(f"C stability: {_pct(result['result_c']['average_stability'])}")
    print(f"E 37-tool accuracy: {_pct(result['result_e']['accuracy'])}")
    print(f"F variant drop: {_signed_pct(result['result_f']['drop'])}")
    print(f"Report: {result['report_path']}")


if __name__ == "__main__":
    main()
