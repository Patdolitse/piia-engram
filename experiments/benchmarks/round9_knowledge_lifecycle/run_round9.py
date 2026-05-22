"""Run Round 9 knowledge lifecycle verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round9_knowledge_lifecycle.llm_judge import LLMJudge, load_live_tools_desc
from experiments.benchmarks.round9_knowledge_lifecycle.scenarios_v9 import validate_scenarios_v9
from experiments.benchmarks.round9_knowledge_lifecycle.test_lifecycle_unit import run_t1
from experiments.benchmarks.round9_knowledge_lifecycle.test_regression import run_t2


ROUND9_DIR = Path(__file__).resolve().parent


def main() -> None:
    args = _parse_args()
    if args.group == "t1":
        run_t1_group()
    elif args.group == "t2":
        run_t2_group()
    elif args.merge_only:
        merge_and_report()
    else:
        run_t1_group()
        run_t2_group()
        merge_and_report()


def run_t1_group() -> dict[str, Any]:
    result = run_t1()
    _write_json(ROUND9_DIR / "results_t1.json", result)
    print(str(ROUND9_DIR / "results_t1.json"))
    return result


def run_t2_group() -> dict[str, Any]:
    validate_scenarios_v9()
    raw_path = ROUND9_DIR / "results_raw.jsonl"
    if raw_path.exists():
        raw_path.unlink()
    tools = load_live_tools_desc()
    if len(tools) != 41:
        raise RuntimeError(f"Expected 41 live MCP tools, got {len(tools)}")
    judge = LLMJudge(raw_log_path=raw_path, runs_per_scenario=3, tools=tools)
    result = run_t2(judge)
    result["judge_info"] = _judge_info(judge, len(tools))
    _write_json(ROUND9_DIR / "results_t2.json", result)
    print(str(ROUND9_DIR / "results_t2.json"))
    return result


def merge_and_report() -> dict[str, Any]:
    t1 = _read_json(ROUND9_DIR / "results_t1.json")
    t2 = _read_json(ROUND9_DIR / "results_t2.json")
    report = render_report(t1, t2)
    (ROUND9_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    print(str(ROUND9_DIR / "REPORT.md"))
    return {"t1": t1, "t2": t2}


def render_report(t1: dict[str, Any], t2: dict[str, Any]) -> str:
    judge_info = t2.get("judge_info", {})
    overall = "**通过**" if t1["passed"] and t2["passed"] else "**不通过**"
    lines = [
        "# Engram Round 9：知识生命周期验证报告",
        "",
        "## 1. 运行说明",
        "",
        "- 范围：验证知识生命周期改动；未修改主代码。",
        "- T1：Python 直接测试 `Engram(root=tempdir)`，不用 LLM，不污染用户数据。",
        f"- T2：DeepSeek `{judge_info.get('model', 'deepseek-chat')}`，从 Round 6 抽取 20 个核心场景，每场景 3 次取多数。",
        f"- T2 工具数：{judge_info.get('tool_count', 0)}（从 `src/piia_engram/mcp_server.py` 实时抽取，应为 41）。",
        f"- T2 调用次数：{judge_info.get('total_calls', 0)}，失败调用：{judge_info.get('failed_calls', 0)}。",
        f"- T2 usage：prompt={judge_info.get('usage', {}).get('prompt_tokens', 0)}，completion={judge_info.get('usage', {}).get('completion_tokens', 0)}，total={judge_info.get('usage', {}).get('total_tokens', 0)} tokens。",
        "",
        "## 2. T1 单元测试结果",
        "",
        f"- 总体：{t1['correct']}/{t1['scenario_count']}，{'pass' if t1['passed'] else 'fail'}",
        "",
        "| 分组 | 结果 | 判定 |",
        "|------|------|------|",
    ]
    for name, group in t1["groups"].items():
        lines.append(f"| {name} | {group['correct']}/{group['total']} | {'pass' if group['passed'] else 'fail'} |")
    lines.extend(
        [
            "",
            "| id | category | input | expected | actual | 正确 |",
            "|----|----------|-------|----------|--------|------|",
        ]
    )
    for row in t1["results"]:
        lines.append(
            f"| {row['id']} | {row['category']} | {_escape(row['input'])} | {_escape(row['expected'])} | {_escape(row['actual'])} | {_mark(row['correct'])} |"
        )
    failed_t1 = [row for row in t1["results"] if not row["correct"]]
    if failed_t1:
        lines.extend(["", "T1 未通过项诊断：", ""])
        for row in failed_t1:
            lines.append(f"- `{row['id']}`：expected `{_escape(row['expected'])}`；actual `{_escape(row['actual'], limit=600)}`。")

    lines.extend(
        [
            "",
            "## 3. T2 回归结果",
            "",
            f"- Round 9：{t2['correct']}/{t2['scenario_count']}（{_pct(t2['accuracy'])}）",
            f"- 回归数：{t2['regression_count']}",
            f"- 判定：{'pass' if t2['passed'] else 'fail'}",
            "",
            "| 来源组 | Round 9 | Round 6 同场景 |",
            "|--------|---------|----------------|",
        ]
    )
    for group, item in t2["by_source_group"].items():
        lines.append(f"| {group} | {item['correct']}/{item['total']} | {item['round6_correct']}/{item['total']} |")
    lines.extend(
        [
            "",
            "| id | expected | actual | Round 6 correct | 回归 | votes | reasoning |",
            "|----|----------|--------|-----------------|------|-------|-----------|",
        ]
    )
    for row in t2["results"]:
        lines.append(
            f"| {row['id']} | {row['expected']} | {row['actual']} | {row['round6_correct']} | {_mark(not row['regressed'])} | {_escape(row.get('votes', []))} | {_escape(row.get('reasoning', ''))} |"
        )

    lines.extend(
        [
            "",
            "## 4. 综合判定",
            "",
            f"{overall}：T1 {'全部通过' if t1['passed'] else '存在失败'}；T2 {'达到门槛' if t2['passed'] else '未达到门槛'}。",
            "",
        ]
    )
    if t2.get("regressions"):
        lines.extend(["回归场景：", ""])
        for row in t2["regressions"]:
            lines.append(f"- `{row['id']}`：expected `{row['expected']}`，actual `{row['actual']}`。")
        lines.append("")
    return "\n".join(lines)


def _judge_info(judge: Any, tool_count: int) -> dict[str, Any]:
    return {
        "model": getattr(getattr(judge, "client", None), "model", "deepseek-chat"),
        "runs_per_scenario": getattr(judge, "runs_per_scenario", 3),
        "total_calls": getattr(judge, "total_calls", 0),
        "failed_calls": getattr(judge, "failed_calls", 0),
        "usage": getattr(judge, "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        "tool_count": tool_count,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["t1", "t2"])
    parser.add_argument("--merge-only", action="store_true")
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _mark(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _escape(value: Any, limit: int = 220) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:limit]


if __name__ == "__main__":
    main()
