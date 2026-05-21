"""Run the round 4 regression benchmark and render the report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.benchmarks.round4_regression.llm_judge import LLMJudge
from experiments.benchmarks.round4_regression.scenarios_r1 import (
    ONBOARDING_SCENARIOS,
    validate_onboarding_scenarios,
)
from experiments.benchmarks.round4_regression.scenarios_r3 import (
    EXTRACTION_SCENARIOS,
    validate_extraction_scenarios,
)
from experiments.benchmarks.round4_regression.test_r1_onboarding import run_r1
from experiments.benchmarks.round4_regression.test_r2_user_context import run_r2
from experiments.benchmarks.round4_regression.test_r3_extraction import run_r3


ROUND4_DIR = Path(__file__).resolve().parent


def run_round4(output_dir: str | Path | None = None, judge: Any | None = None) -> dict[str, Any]:
    validate_onboarding_scenarios(ONBOARDING_SCENARIOS)
    validate_extraction_scenarios(EXTRACTION_SCENARIOS)

    target_dir = Path(output_dir) if output_dir else ROUND4_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if judge is None:
        raw_path = target_dir / "results_raw.jsonl"
        if raw_path.exists():
            raw_path.unlink()
        judge = LLMJudge(raw_log_path=raw_path, runs_per_scenario=3)

    r1 = run_r1(judge)
    r2 = run_r2(judge)
    r3 = run_r3(judge)

    _write_json(target_dir / "results_r1.json", r1)
    _write_json(target_dir / "results_r2.json", r2)
    _write_json(target_dir / "results_r3.json", r3)

    judge_info = {
        "model": getattr(getattr(judge, "client", None), "model", "deepseek-chat"),
        "temperature": 0.0,
        "runs_per_scenario": getattr(judge, "runs_per_scenario", 3),
        "total_calls": getattr(judge, "total_calls", 0),
        "failed_calls": getattr(judge, "failed_calls", 0),
        "usage": getattr(judge, "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    }
    report = render_report(r1, r2, r3, judge_info)
    (target_dir / "REPORT.md").write_text(report, encoding="utf-8")
    return {
        "r1": r1,
        "r2": r2,
        "r3": r3,
        "judge_info": judge_info,
        "report_path": str(target_dir / "REPORT.md"),
    }


def render_report(
    r1: dict[str, Any],
    r2: dict[str, Any],
    r3: dict[str, Any],
    judge_info: dict[str, Any],
) -> str:
    r1_summary = r1["summary"]
    r2_summary = r2["summary"]
    r3_summary = r3["summary"]
    usage = judge_info["usage"]
    issues = _collect_issues(r1, r2, r3)

    lines = [
        "# Engram 第四轮回归基准报告",
        "",
        "## 1. 运行说明",
        "",
        f"- LLM：DeepSeek（`{judge_info['model']}`）",
        f"- 温度：{judge_info['temperature']}",
        f"- 每个评估场景：{judge_info['runs_per_scenario']} 次取多数或中位数",
        f"- 总调用次数：{judge_info['total_calls']}",
        f"- 失败/UNKNOWN 调用：{judge_info['failed_calls']}",
        f"- Usage：prompt={usage.get('prompt_tokens', 0)}，completion={usage.get('completion_tokens', 0)}，total={usage.get('total_tokens', 0)} tokens",
        "- 成本：记录 API usage，不按实时价格换算；实际费用以 DeepSeek 控制台为准。",
        f"- R1 场景数：{r1['scenario_count']}；R2 调用数：{r2['call_count']}；R2 相似度对数：{len(r2['similarity_pairs'])}；R3 场景数：{r3['scenario_count']}",
        "- 边界：本轮只新增 `experiments/benchmarks/round4_regression/`，未修改主代码或前三轮 benchmark。",
        "",
        "## 2. R1 onboarding 数据完整性",
        "",
        "| 用户类型 | role | tech stack | lesson search | language | 说明 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in r1["rows"]:
        passes = row["passes"]
        lines.append(
            "| {id} | {role} | {tech} | {lesson} | {lang} | {reason} |".format(
                id=row["id"],
                role=_mark(passes["role_readable"]),
                tech=_mark(passes["tech_stack_readable"]),
                lesson=_mark(passes["lesson_searchable"]),
                lang=_mark(passes["language_ok"]),
                reason=_escape(row["judgment"].get("reasoning", "")),
            )
        )
    lines.extend(
        [
            "",
            f"- role 可读：{r1_summary['role_readable']}/5",
            f"- tech stack 可读：{r1_summary['tech_stack_readable']}/5",
            f"- lesson 可检索：{r1_summary['lesson_searchable']}/5",
            f"- 中文组：{r1_summary['language']['zh']['passed']}/{r1_summary['language']['zh']['total']}；英文组：{r1_summary['language']['en']['passed']}/{r1_summary['language']['en']['total']}",
            f"- R1 判定：{_mark(r1_summary['passed'])}",
            "",
            "## 3. R2 get_user_context 一致性",
            "",
            f"- 10 次输出 byte-identical：{_mark(r2_summary['byte_identical'])}",
            f"- get_user_context/generate_context 调用 LLM：否。当前实现为本地拼装，因此理论上应稳定。",
            f"- identity 完整：{r2_summary['identity_complete']}/10",
            f"- 关键 lesson 提及：{r2_summary['key_lesson_mentioned']}/10",
            f"- 关键 decision 提及：{r2_summary['key_decision_mentioned']}/10",
            f"- 关键知识整体提及：{r2_summary['key_knowledge_mentioned']}/10",
            f"- 最低语义相似度：{r2_summary['min_similarity']:.2f}",
            f"- R2 判定：{_mark(r2_summary['passed'])}",
            "",
            "相似度矩阵：",
            "",
        ]
    )
    lines.extend(_render_similarity_matrix(r2["similarity_matrix"]))
    lines.extend(
        [
            "",
            "## 4. R3 extract_session_insights 质量",
            "",
            f"- Recall：{_pct(r3_summary['recall'])}",
            f"- Precision：{_pct(r3_summary['precision'])}",
            f"- 语义准确率：{_pct(r3_summary['semantic_accuracy'])}",
            f"- 普通讨论 false positive：{r3_summary['false_positive_count']}/5",
            f"- R3 判定：{_mark(r3_summary['passed'])}",
            "",
            "| id | 类型 | saved | relevant | false positive | semantic | 说明 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in r3["rows"]:
        judgment = row["judgment"]
        lines.append(
            "| {id} | {category} | {saved} | {rel} | {fp} | {sem} | {reason} |".format(
                id=row["id"],
                category=row["category"],
                saved=row["saved_count"],
                rel=_mark(bool(judgment.get("extracted_relevant"))),
                fp=_mark(not row["false_positive"]),
                sem=_pct(row["semantic_accuracy"]),
                reason=_escape(judgment.get("reasoning", "")),
            )
        )

    lines.extend(
        [
            "",
            "## 5. 发现的问题清单",
            "",
        ]
    )
    lines.extend(_render_issues(issues))
    lines.extend(
        [
            "",
            "## 6. 回归基准",
            "",
            "```yaml",
            "regression_baseline:",
            "  r1_onboarding:",
            f"    role_writeback: \"{r1_summary['role_readable']}/5\"",
            f"    tech_stack_writeback: \"{r1_summary['tech_stack_readable']}/5\"",
            f"    lesson_searchable: \"{r1_summary['lesson_searchable']}/5\"",
            f"    zh_language_ok: \"{r1_summary['language']['zh']['passed']}/{r1_summary['language']['zh']['total']}\"",
            f"    en_language_ok: \"{r1_summary['language']['en']['passed']}/{r1_summary['language']['en']['total']}\"",
            f"    passed: {str(r1_summary['passed']).lower()}",
            "  r2_user_context:",
            f"    identity_complete: \"{r2_summary['identity_complete']}/10\"",
            f"    key_lesson_mentioned: \"{r2_summary['key_lesson_mentioned']}/10\"",
            f"    key_decision_mentioned: \"{r2_summary['key_decision_mentioned']}/10\"",
            f"    key_knowledge_mentioned: \"{r2_summary['key_knowledge_mentioned']}/10\"",
            f"    semantic_consistency_min: {r2_summary['min_similarity']:.2f}",
            f"    byte_identical: {str(r2_summary['byte_identical']).lower()}",
            f"    passed: {str(r2_summary['passed']).lower()}",
            "  r3_extraction:",
            f"    recall: \"{_pct(r3_summary['recall'])}\"",
            f"    precision: \"{_pct(r3_summary['precision'])}\"",
            f"    semantic_accuracy: \"{_pct(r3_summary['semantic_accuracy'])}\"",
            f"    false_positive: \"{r3_summary['false_positive_count']}/5\"",
            f"    passed: {str(r3_summary['passed']).lower()}",
            "```",
            "",
            "这些数字就是后续主代码重构后的对比门槛；下降项应优先解释或修复。",
        ]
    )
    return "\n".join(lines)


def _collect_issues(r1: dict[str, Any], r2: dict[str, Any], r3: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if r1["summary"]["role_readable"] < 5 or r1["summary"]["tech_stack_readable"] < 5:
        issues.append(
            {
                "priority": "P0 - 必须修复",
                "description": "onboarding 写入的身份字段无法稳定从冷启动上下文读回。",
                "impact": "新用户第一次接入后，AI 可能拿不到角色或技术栈，冷启动体验失真。",
                "repro": "运行 R1 onboarding 场景，查看失败用户类型的 get_user_context 输出。",
                "location": "src/engram_core/setup_wizard.py:223, src/engram_core/core.py:1608",
            }
        )
    if r1["summary"]["lesson_searchable"] < 4:
        issues.append(
            {
                "priority": "P0 - 必须修复",
                "description": "onboarding 种子 lesson 写入后无法稳定检索。",
                "impact": "用户录入的基础偏好无法被后续工具召回。",
                "repro": "运行 R1 后对 primary_search_query 调用 search_knowledge(scope='lessons')。",
                "location": "src/engram_core/core.py:523, src/engram_core/core.py:616",
            }
        )
    if r2["summary"]["key_decision_mentioned"] < 8:
        issues.append(
            {
                "priority": "P1 - 建议修复",
                "description": "get_user_context 冷启动上下文没有稳定包含近期 decision。",
                "impact": "AI 会记得 lesson，但可能不知道用户已经做过的关键取舍。",
                "repro": "运行 R2；临时 Engram 中有 3 条 decision，但 generate_context 只拼装 profile/preferences/lessons/project。",
                "location": "src/engram_core/core.py:1608",
            }
        )
    if r2["summary"]["min_similarity"] < 0.85:
        issues.append(
            {
                "priority": "P1 - 建议修复",
                "description": "同一 Engram 状态下 get_user_context 多次输出语义不稳定。",
                "impact": "不同 AI 会拿到漂移的冷启动信息。",
                "repro": "运行 R2 并查看 similarity_matrix 中低于 0.85 的 pair。",
                "location": "src/engram_core/core.py:1608, src/engram_core/core.py:660",
            }
        )
    if r3["summary"]["recall"] < 0.80:
        issues.append(
            {
                "priority": "P1 - 建议修复",
                "description": "extract_session_insights 对应沉淀的 lesson/decision 召回不足。",
                "impact": "用户对话中的关键知识会漏存。",
                "repro": "运行 R3，查看 recall_hit=false 的 lesson/decision 片段。",
                "location": "src/engram_core/core.py:1417",
            }
        )
    if r3["summary"]["precision"] < 0.80 or r3["summary"]["false_positive_count"] > 2:
        issues.append(
            {
                "priority": "P1 - 建议修复",
                "description": "extract_session_insights 会把普通讨论误存为知识。",
                "impact": "Engram 可能被临时协作噪声污染。",
                "repro": "运行 R3 普通讨论组，查看 false_positive=true 的片段。",
                "location": "src/engram_core/core.py:1417",
            }
        )
    if not issues:
        issues.append(
            {
                "priority": "P2 - 改进项",
                "description": "本轮未发现硬失败；建议把 round4 作为后续重构固定回归门槛。",
                "impact": "可以防止冷启动、检索、自动沉淀路径在重构时静默退化。",
                "repro": "后续主代码改动后重新运行 `python -m experiments.benchmarks.round4_regression.run_round4`。",
                "location": "experiments/benchmarks/round4_regression/run_round4.py",
            }
        )
    return issues


def _render_issues(issues: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    by_priority = {"P0 - 必须修复": [], "P1 - 建议修复": [], "P2 - 改进项": []}
    for issue in issues:
        by_priority.setdefault(issue["priority"], []).append(issue)
    for priority in ["P0 - 必须修复", "P1 - 建议修复", "P2 - 改进项"]:
        lines.append(f"**{priority}**：")
        current = by_priority.get(priority, [])
        if not current:
            lines.append("- 无")
            lines.append("")
            continue
        for issue in current:
            lines.extend(
                [
                    f"- 描述：{issue['description']}",
                    f"- 影响范围：{issue['impact']}",
                    f"- 复现步骤：{issue['repro']}",
                    f"- 涉及代码位置：{issue['location']}",
                    "",
                ]
            )
    return lines


def _render_similarity_matrix(matrix: list[list[float]]) -> list[str]:
    headers = ["call"] + [str(index + 1) for index in range(len(matrix))]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for index, row in enumerate(matrix, start=1):
        cells = [str(index)] + [f"{float(value):.2f}" for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]


if __name__ == "__main__":
    result = run_round4()
    print(result["report_path"])
