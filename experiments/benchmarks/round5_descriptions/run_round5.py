"""Run Round 5 tool-description verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round5_descriptions.llm_judge import LLMJudge, load_live_tools_desc
from experiments.benchmarks.round5_descriptions.scenarios_v5 import validate_scenarios_v5
from experiments.benchmarks.round5_descriptions.test_t1_near_synonym import run_t1
from experiments.benchmarks.round5_descriptions.test_t2_new_tools import run_t2
from experiments.benchmarks.round5_descriptions.test_t3_regression import run_t3


ROUND5_DIR = Path(__file__).resolve().parent
GROUP_RAW = {
    "t1": "results_raw_t1.jsonl",
    "t2": "results_raw_t2.jsonl",
    "t3": "results_raw_t3.jsonl",
}


def main() -> None:
    args = _parse_args()
    if args.group:
        run_group(args.group, output_dir=args.output_dir)
    elif args.merge_only:
        merge_and_report(output_dir=args.output_dir)
    else:
        for group in ("t1", "t2", "t3"):
            run_group(group, output_dir=args.output_dir)
        merge_and_report(output_dir=args.output_dir)


def run_group(group: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    validate_scenarios_v5()
    target_dir = Path(output_dir) if output_dir else ROUND5_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_path = target_dir / GROUP_RAW[group]
    if raw_path.exists():
        raw_path.unlink()
    tools = load_live_tools_desc()
    judge = LLMJudge(raw_log_path=raw_path, runs_per_scenario=3, tools=tools)

    if group == "t1":
        result = run_t1(judge)
        name = "results_t1.json"
    elif group == "t2":
        result = run_t2(judge)
        name = "results_t2.json"
    elif group == "t3":
        result = run_t3(judge)
        name = "results_t3.json"
    else:
        raise ValueError(f"Unknown group: {group}")

    result["judge_info"] = _judge_info(judge, len(tools))
    _write_json(target_dir / name, result)
    print(str(target_dir / name))
    return result


def merge_and_report(output_dir: str | Path | None = None) -> dict[str, Any]:
    target_dir = Path(output_dir) if output_dir else ROUND5_DIR
    t1 = _read_json(target_dir / "results_t1.json")
    t2 = _read_json(target_dir / "results_t2.json")
    t3 = _read_json(target_dir / "results_t3.json")
    raw_path = target_dir / "results_raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as out:
        for group in ("t1", "t2", "t3"):
            path = target_dir / GROUP_RAW[group]
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    out.write(line + "\n")

    judge_info = _merge_judge_info([t1.get("judge_info", {}), t2.get("judge_info", {}), t3.get("judge_info", {})])
    report = render_report(t1, t2, t3, judge_info)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print(str(target_dir / "REPORT.md"))
    return {"t1": t1, "t2": t2, "t3": t3, "judge_info": judge_info}


def render_report(t1: dict[str, Any], t2: dict[str, Any], t3: dict[str, Any], judge_info: dict[str, Any]) -> str:
    overall = _overall_judgment(t1, t2, t3)
    lines = [
        "# Engram Round 5 工具描述优化与快捷工具验证报告",
        "",
        "## 1. 运行说明",
        "",
        f"- LLM：DeepSeek（`{judge_info.get('model', 'deepseek-chat')}`）",
        "- 温度：0.0",
        "- 每个场景：3 次取多数",
        f"- 当前工具数：{judge_info.get('tool_count', 0)}（从 `src/piia_engram/mcp_server.py` 实时抽取）",
        f"- 总场景数：{t1['scenario_count'] + t2['scenario_count'] + t3['scenario_count']}",
        f"- 总调用次数：{judge_info.get('total_calls', 0)}",
        f"- 失败调用：{judge_info.get('failed_calls', 0)}",
        f"- Usage：prompt={judge_info['usage'].get('prompt_tokens', 0)}，completion={judge_info['usage'].get('completion_tokens', 0)}，total={judge_info['usage'].get('total_tokens', 0)} tokens",
        "- 边界：本轮只验证主代码中已经完成的工具描述和快捷工具，不修改 `mcp_server.py`。",
        "",
        "## 2. T1 近义混淆结果",
        "",
        "| 组 | Round 3 | Round 5 | 差异 | 判定 |",
        "|----|---------|---------|------|------|",
    ]
    for group in t1["groups"]:
        lines.append(
            f"| {group['group']} | {_pct(group['round3_baseline'])} | {_pct(group['accuracy'])} | {_signed_pct(group['delta'])} | {group['judgment']} |"
        )
    lines.extend(
        [
            f"| 总体 | {_pct(t1['round3_baseline'])} | {_pct(t1['accuracy'])} | {_signed_pct(t1['delta'])} | {t1['judgment']} |",
            "",
            "## 3. T2 新工具识别结果",
            "",
            "| 指标 | 结果 | 判定 |",
            "|------|------|------|",
            f"| wrap_up_session 正确率 | {_pct(t2['wrap_up_session_accuracy'])} | {t2['wrap_up_session_judgment']} |",
            f"| start_project 正确率 | {_pct(t2['start_project_accuracy'])} | {t2['start_project_judgment']} |",
            f"| T2 总体正确率 | {_pct(t2['accuracy'])} | {_three_band(t2['accuracy'], 0.80, 0.60)} |",
            "",
            "| id | expected | actual | 正确 | 说明 |",
            "|----|----------|--------|------|------|",
        ]
    )
    for row in t2["results"]:
        lines.append(f"| {row['id']} | {row['expected']} | {row['actual']} | {_mark(row['correct'])} | {_escape(row['reasoning'])} |")

    lines.extend(
        [
            "",
            "## 4. T3 回归结果",
            "",
            f"- 正确率：{t3['correct']}/{t3['scenario_count']}（{_pct(t3['accuracy'])}）",
            f"- 回归场景数：{t3['regression_count']}",
            f"- 原子工具场景误选快捷工具：{t3['shortcut_false_positive_count']}/15",
            "",
            "| 类别 | 正确率 |",
            "|------|--------|",
        ]
    )
    for category, item in t3["by_category"].items():
        lines.append(f"| {category} | {item['correct']}/{item['total']}（{_pct(item['accuracy'])}） |")
    lines.extend(
        [
            "",
            "| id | expected | actual | 回归 | 说明 |",
            "|----|----------|--------|------|------|",
        ]
    )
    for row in t3["results"]:
        lines.append(f"| {row['id']} | {row['expected']} | {row['actual']} | {_mark(not row['regressed'])} | {_escape(row['reasoning'])} |")

    lines.extend(
        [
            "",
            "## 5. 综合判定",
            "",
            overall,
            "",
            "## 6. 与 Round 3 对比总结",
            "",
            f"- Round 3 Test E 总体：90.0%；Round 5 T1 总体：{_pct(t1['accuracy'])}（{_signed_pct(t1['delta'])}）。",
            f"- Round 5 新工具识别：wrap_up_session={_pct(t2['wrap_up_session_accuracy'])}，start_project={_pct(t2['start_project_accuracy'])}。",
            f"- Round 5 T3 选取 15 个核心旧场景，回归数={t3['regression_count']}。",
            "- `results_raw.jsonl` 合并保存了 T1/T2/T3 的所有原始 request/response。",
            "",
        ]
    )
    return "\n".join(lines)


def _overall_judgment(t1: dict[str, Any], t2: dict[str, Any], t3: dict[str, Any]) -> str:
    t1_ok = all(group["accuracy"] >= 0.75 for group in t1["groups"]) and t1["accuracy"] >= t1["round3_baseline"]
    t2_ok = t2["wrap_up_session_accuracy"] >= 0.80 and t2["start_project_accuracy"] >= 0.80
    t3_ok = t3["regression_count"] == 0
    if t1_ok and t2_ok and t3_ok:
        return "**通过**：工具描述优化没有造成已测回归，新快捷工具可以被 DeepSeek 正确识别。"
    findings = ["**不通过/部分通过**：至少一个测试组未达门槛。"]
    if not t1_ok:
        findings.append(f"- T1 未达门槛：总体 {_pct(t1['accuracy'])}，Round 3 基线 {_pct(t1['round3_baseline'])}。")
    if not t2_ok:
        findings.append(
            f"- T2 未达门槛：wrap_up_session={_pct(t2['wrap_up_session_accuracy'])}，start_project={_pct(t2['start_project_accuracy'])}。"
        )
    if not t3_ok:
        findings.append(
            f"- T3 发现 {t3['regression_count']} 个回归，且原子工具场景误选快捷工具 {t3['shortcut_false_positive_count']}/15。"
        )
        for row in t3.get("regressions", []):
            findings.append(
                f"- 回归案例：{row['id']} 期望 `{row['expected']}`，实际 `{row['actual']}`；说明新快捷工具可能覆盖了只想继承知识、但不想创建项目快照的场景。"
            )
    return "\n".join(findings)


def _judge_info(judge: Any, tool_count: int) -> dict[str, Any]:
    return {
        "model": getattr(getattr(judge, "client", None), "model", "deepseek-chat"),
        "runs_per_scenario": getattr(judge, "runs_per_scenario", 3),
        "total_calls": getattr(judge, "total_calls", 0),
        "failed_calls": getattr(judge, "failed_calls", 0),
        "usage": getattr(judge, "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        "tool_count": tool_count,
    }


def _merge_judge_info(items: list[dict[str, Any]]) -> dict[str, Any]:
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for item in items:
        for key in usage:
            usage[key] += int(item.get("usage", {}).get(key, 0) or 0)
    return {
        "model": next((item.get("model") for item in items if item.get("model")), "deepseek-chat"),
        "runs_per_scenario": 3,
        "total_calls": sum(int(item.get("total_calls", 0) or 0) for item in items),
        "failed_calls": sum(int(item.get("failed_calls", 0) or 0) for item in items),
        "usage": usage,
        "tool_count": max(int(item.get("tool_count", 0) or 0) for item in items),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["t1", "t2", "t3"])
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}pp"


def _mark(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:220]


def _three_band(value: float, pass_at: float, warn_at: float) -> str:
    if value >= pass_at:
        return "pass"
    if value >= warn_at:
        return "gray"
    return "fail"


if __name__ == "__main__":
    main()
