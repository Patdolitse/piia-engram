"""Run Round 6 full-coverage MCP tool-selection verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round6_full_coverage.llm_judge import LLMJudge, load_live_tools_desc
from experiments.benchmarks.round6_full_coverage.scenarios_v6 import (
    ALL_EXPECTED_TOOLS,
    GROUP_SCENARIOS,
    validate_scenarios_v6,
)
from experiments.benchmarks.round6_full_coverage.test_g1_identity import run_g1
from experiments.benchmarks.round6_full_coverage.test_g2_knowledge import run_g2
from experiments.benchmarks.round6_full_coverage.test_g3_maintenance import run_g3
from experiments.benchmarks.round6_full_coverage.test_g4_boundary import run_g4


ROUND6_DIR = Path(__file__).resolve().parent
GROUP_RAW = {
    "g1": "results_raw_g1.jsonl",
    "g2": "results_raw_g2.jsonl",
    "g3": "results_raw_g3.jsonl",
    "g4": "results_raw_g4.jsonl",
}
GROUP_RESULTS = {
    "g1": "results_g1.json",
    "g2": "results_g2.json",
    "g3": "results_g3.json",
    "g4": "results_g4.json",
}


def main() -> None:
    args = _parse_args()
    if args.group:
        run_group(args.group, output_dir=args.output_dir)
    elif args.merge_only:
        merge_and_report(output_dir=args.output_dir)
    else:
        for group in ("g1", "g2", "g3", "g4"):
            run_group(group, output_dir=args.output_dir)
        merge_and_report(output_dir=args.output_dir)


def run_group(group: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    validate_scenarios_v6()
    target_dir = Path(output_dir) if output_dir else ROUND6_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_path = target_dir / GROUP_RAW[group]
    if raw_path.exists():
        raw_path.unlink()

    tools = load_live_tools_desc()
    if len(tools) != 39:
        raise RuntimeError(f"Expected 39 live MCP tools, got {len(tools)}")

    judge = LLMJudge(raw_log_path=raw_path, runs_per_scenario=3, tools=tools)
    if group == "g1":
        result = run_g1(judge)
    elif group == "g2":
        result = run_g2(judge)
    elif group == "g3":
        result = run_g3(judge)
    elif group == "g4":
        result = run_g4(judge)
    else:
        raise ValueError(f"Unknown group: {group}")

    result["judge_info"] = _judge_info(judge, len(tools))
    result["scenario_ids"] = [item["id"] for item in GROUP_SCENARIOS[group]]
    _write_json(target_dir / GROUP_RESULTS[group], result)
    print(str(target_dir / GROUP_RESULTS[group]))
    return result


def merge_and_report(output_dir: str | Path | None = None) -> dict[str, Any]:
    target_dir = Path(output_dir) if output_dir else ROUND6_DIR
    results = {group: _read_json(target_dir / filename) for group, filename in GROUP_RESULTS.items()}
    raw_path = target_dir / "results_raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as out:
        for group in ("g1", "g2", "g3", "g4"):
            path = target_dir / GROUP_RAW[group]
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    out.write(line + "\n")

    judge_info = _merge_judge_info([item.get("judge_info", {}) for item in results.values()])
    report = render_report(results["g1"], results["g2"], results["g3"], results["g4"], judge_info)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print(str(target_dir / "REPORT.md"))
    return {**results, "judge_info": judge_info}


def render_report(
    g1: dict[str, Any],
    g2: dict[str, Any],
    g3: dict[str, Any],
    g4: dict[str, Any],
    judge_info: dict[str, Any],
) -> str:
    overall = _overall_judgment(g1, g2, g3, g4)
    all_tool_rows = _all_tool_rows(g1, g2, g3)
    failing_rows = [row for row in g1["results"] + g2["results"] + g3["results"] + g4["results"] if not row["correct"]]
    lines = [
        "# Engram Round 6：39 工具全量覆盖自测报告",
        "",
        "## 1. 运行说明",
        "",
        f"- LLM：DeepSeek `{judge_info.get('model', 'deepseek-chat')}`",
        "- 温度：0.0",
        "- 每个场景：3 次调用取多数",
        f"- 当前工具数：{judge_info.get('tool_count', 0)}（从 `src/piia_engram/mcp_server.py` 实时抽取）",
        f"- 总场景数：{g1['scenario_count'] + g2['scenario_count'] + g3['scenario_count'] + g4['scenario_count']}",
        f"- 总调用次数：{judge_info.get('total_calls', 0)}",
        f"- 失败调用：{judge_info.get('failed_calls', 0)}",
        f"- Usage：prompt={judge_info['usage'].get('prompt_tokens', 0)}，completion={judge_info['usage'].get('completion_tokens', 0)}，total={judge_info['usage'].get('total_tokens', 0)} tokens",
        "- 边界：本轮只做验证；未修改 `src/piia_engram/mcp_server.py` 主代码。",
        "- 原始响应：`results_raw.jsonl` 合并保存全部 request/response/raw_content/parsed/error。",
        "",
        "## 2. G1 Identity & Context 结果",
        "",
        _group_summary_line(g1),
        "",
        *_tool_table(g1["by_tool"]),
        "",
        "## 3. G2 Knowledge Recording & Retrieval 结果",
        "",
        _group_summary_line(g2),
        "",
        *_tool_table(g2["by_tool"]),
        "",
        "与 Round 5 近义组对比：",
        "",
        *_round5_comparison_table(g1, g2),
        "",
        "## 4. G3 Maintenance, Session & Workflow 结果",
        "",
        _group_summary_line(g3),
        f"- 快捷工具误抢原子工具：{g3.get('shortcut_false_positive_count', 0)}",
        f"- 快捷工具场景误选原子工具：{g3.get('shortcut_false_negative_count', 0)}",
        "",
        *_tool_table(g3["by_tool"]),
        "",
        "快捷工具 vs 原子工具混淆：",
        "",
        *_shortcut_confusion_table(g3),
        "",
        "## 5. G4 边界场景结果",
        "",
        f"- no-tool：{g4['no_tool']['correct']}/{g4['no_tool']['total']}（{_pct(g4['no_tool']['accuracy'])}，{g4['no_tool']['judgment']}）",
        f"- 缺参数：{g4['missing_params']['correct']}/{g4['missing_params']['total']}（{_pct(g4['missing_params']['accuracy'])}，{g4['missing_params']['judgment']}）",
        "",
        *_boundary_table(g4),
        "",
        "## 6. 综合判定",
        "",
        overall,
        "",
        "## 7. 全量覆盖热力图",
        "",
        *_heatmap_table(all_tool_rows),
        "",
        "需要进一步优化 docstring 的工具：",
        "",
        *_docstring_followups(all_tool_rows),
        "",
        "## 8. 与 Round 3/5 对比",
        "",
        "- Round 3 Test E：37 工具近义场景总体 90.0%。",
        "- Round 5 T1：近义场景总体 100.0%；T2：`wrap_up_session` 与 `start_project` 均 100.0%；T3：14/15，发现 1 个快捷工具抢占回归。",
        f"- Round 6：覆盖 39/39 工具，每个工具 2 个场景；G1={_pct(g1['accuracy'])}，G2={_pct(g2['accuracy'])}，G3={_pct(g3['accuracy'])}，G4={_pct(g4['accuracy'])}。",
        "- Round 6 新增了 `none` 选项以测试闲聊/澄清/感谢类 no-tool 场景，并加入缺参数场景以观察模型是否会乱猜参数。",
        "",
    ]
    if failing_rows:
        lines.extend(
            [
                "## 附：失败场景明细",
                "",
                "| id | expected | actual | votes | reasoning |",
                "|----|----------|--------|-------|-----------|",
            ]
        )
        for row in failing_rows:
            lines.append(
                f"| {row['id']} | {row['expected']} | {row['actual']} | {_escape(row.get('votes', []))} | {_escape(row.get('reasoning', ''))} |"
            )
        lines.append("")
    return "\n".join(lines)


def _group_summary_line(result: dict[str, Any]) -> str:
    zero_count = len(result.get("zero_of_two_tools", []))
    return (
        f"- 总体：{result['correct']}/{result['scenario_count']}（{_pct(result['accuracy'])}，"
        f"{result['judgment']}）；0/2 工具数：{zero_count}"
    )


def _tool_table(by_tool: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 工具 | 正确 | 准确率 | 判定 |",
        "|------|------|--------|------|",
    ]
    for item in by_tool:
        lines.append(
            f"| `{item['tool']}` | {item['correct']}/{item['total']} | {_pct(item['accuracy'])} | {item['status']} |"
        )
    return lines


def _round5_comparison_table(g1: dict[str, Any], g2: dict[str, Any]) -> list[str]:
    current = {item["tool"]: item for item in g1["by_tool"] + g2["by_tool"]}
    groups = {
        "lesson_decision_extract": ["add_lesson", "add_decision", "extract_session_insights"],
        "search_relevant_similar": ["search_knowledge", "get_relevant_knowledge", "find_similar_knowledge"],
        "snapshot_context_user": ["save_project_snapshot", "get_project_context", "get_user_context"],
    }
    lines = [
        "| Round 5 近义组 | Round 5 | Round 6 覆盖工具 | Round 6 | 差异 |",
        "|----------------|---------|------------------|---------|------|",
    ]
    for group, tools in groups.items():
        total = sum(current[tool]["total"] for tool in tools)
        correct = sum(current[tool]["correct"] for tool in tools)
        accuracy = correct / total
        baseline = 1.0
        lines.append(
            f"| {group} | {_pct(baseline)} | {', '.join(f'`{tool}`' for tool in tools)} | {_pct(accuracy)} | {_signed_pp(accuracy - baseline)} |"
        )
    return lines


def _shortcut_confusion_table(g3: dict[str, Any]) -> list[str]:
    confusions = g3.get("shortcut_confusions", [])
    if not confusions:
        return ["无快捷工具与原子工具混淆。"]
    lines = [
        "| id | expected | actual | votes | reasoning |",
        "|----|----------|--------|-------|-----------|",
    ]
    for row in confusions:
        lines.append(
            f"| {row['id']} | {row['expected']} | {row['actual']} | {_escape(row.get('votes', []))} | {_escape(row.get('reasoning', ''))} |"
        )
    return lines


def _boundary_table(g4: dict[str, Any]) -> list[str]:
    lines = [
        "| id | 类型 | expected | actual | 正确 | reasoning |",
        "|----|------|----------|--------|------|-----------|",
    ]
    for row in g4["results"]:
        lines.append(
            f"| {row['id']} | {row['boundary_type']} | {row['expected']} | {row['actual']} | {_mark(row['correct'])} | {_escape(row.get('reasoning', ''))} |"
        )
    return lines


def _all_tool_rows(g1: dict[str, Any], g2: dict[str, Any], g3: dict[str, Any]) -> list[dict[str, Any]]:
    by_name = {item["tool"]: item for item in g1["by_tool"] + g2["by_tool"] + g3["by_tool"]}
    rows = []
    for tool in ALL_EXPECTED_TOOLS:
        item = by_name[tool]
        rows.append(
            {
                "tool": tool,
                "correct": item["correct"],
                "total": item["total"],
                "accuracy": item["accuracy"],
                "status": "OK" if item["correct"] == 2 else "WARN" if item["correct"] == 1 else "FAIL",
            }
        )
    return rows


def _heatmap_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 工具 | 结果 | 准确率 | 状态 |",
        "|------|------|--------|------|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['tool']}` | {row['correct']}/{row['total']} | {_pct(row['accuracy'])} | {row['status']} |"
        )
    return lines


def _docstring_followups(rows: list[dict[str, Any]]) -> list[str]:
    needs = [row for row in rows if row["correct"] < row["total"]]
    if not needs:
        return ["- 暂无：39 个工具在本轮每工具 2 场景中均未出现错误。"]
    return [
        f"- `{row['tool']}`：{row['correct']}/{row['total']}，建议补强与相邻工具的边界描述。"
        for row in needs
    ]


def _overall_judgment(g1: dict[str, Any], g2: dict[str, Any], g3: dict[str, Any], g4: dict[str, Any]) -> str:
    judgments = [g1["judgment"], g2["judgment"], g3["judgment"], g4["judgment"]]
    if all(item == "pass" for item in judgments):
        return "**通过**：G1/G2/G3 均达到全量工具选择门槛，G4 no-tool 与缺参数场景也达到通过线。"
    if "fail" in judgments:
        return (
            "**不通过**：至少一个测试组低于门槛。"
            f" G1={g1['judgment']}，G2={g2['judgment']}，G3={g3['judgment']}，G4={g4['judgment']}。"
        )
    return (
        "**部分通过**：没有测试组直接失败，但至少一个测试组处于灰色区。"
        f" G1={g1['judgment']}，G2={g2['judgment']}，G3={g3['judgment']}，G4={g4['judgment']}。"
    )


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
    parser.add_argument("--group", choices=["g1", "g2", "g3", "g4"])
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _signed_pp(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}pp"


def _mark(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]


if __name__ == "__main__":
    main()
