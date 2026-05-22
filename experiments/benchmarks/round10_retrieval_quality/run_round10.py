"""Run Round 10 retrieval quality benchmark.

Usage:
    python run_round10.py              # Run all (T1 then T2, then merge)
    python run_round10.py --group t1   # Deterministic only (no API)
    python run_round10.py --group t2   # LLM evaluation only (needs DeepSeek)
    python run_round10.py --merge-only # Combine results into REPORT.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure src and repo root are on path
_repo = Path(__file__).resolve().parent.parent.parent.parent
_src = _repo / "src"
for _p in [str(_src), str(_repo)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

ROUND10_DIR = Path(__file__).resolve().parent


def main() -> None:
    args = _parse_args()
    if args.merge_only:
        merge_and_report()
    elif args.group == "t1":
        run_t1_group()
    elif args.group == "t2":
        run_t2_group()
    else:
        run_t1_group()
        run_t2_group()
        merge_and_report()


def run_t1_group() -> dict[str, Any]:
    """Run all deterministic tests (D1, D2, D3_det, D5, D6)."""
    from experiments.benchmarks.round10_retrieval_quality.test_d1_context_assembly import run_d1
    from experiments.benchmarks.round10_retrieval_quality.test_d2_token_budget import run_d2
    from experiments.benchmarks.round10_retrieval_quality.test_d3_recall_precision import run_d3_det
    from experiments.benchmarks.round10_retrieval_quality.test_d5_stale_conflict import run_d5
    from experiments.benchmarks.round10_retrieval_quality.test_d6_search_scoring import run_d6

    dimensions = {}
    all_results = []

    for name, runner in [("D1", run_d1), ("D2", run_d2), ("D3_det", run_d3_det),
                          ("D5", run_d5), ("D6", run_d6)]:
        print(f"  Running {name}...", end=" ", flush=True)
        result = runner()
        dimensions[name] = result
        all_results.extend(result["results"])
        status = "PASS" if result["passed"] else "FAIL"
        print(f"{status} ({result['correct']}/{result['total']})")

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    t1_result = {
        "test": "T1",
        "timestamp": datetime.now().isoformat(),
        "scenario_count": total,
        "correct": correct,
        "passed": all(d["passed"] for d in dimensions.values()),
        "dimensions": dimensions,
    }
    _write_json(ROUND10_DIR / "results_t1.json", t1_result)
    print(f"\n  T1 total: {correct}/{total} — {'PASS' if t1_result['passed'] else 'FAIL'}")
    print(f"  Saved to {ROUND10_DIR / 'results_t1.json'}")
    return t1_result


def run_t2_group() -> dict[str, Any]:
    """Run LLM evaluation tests (D3_llm, D4) using DeepSeek V4 Pro."""
    from experiments.benchmarks.round10_retrieval_quality.llm_judge import (
        DeepSeekJudge, MODEL_JUDGE,
    )
    from experiments.benchmarks.round10_retrieval_quality.test_d3_recall_quality import run_d3_llm
    from experiments.benchmarks.round10_retrieval_quality.test_d4_identity_fidelity import run_d4

    raw_path = ROUND10_DIR / "results_raw.jsonl"
    if raw_path.exists():
        raw_path.unlink()

    judge = DeepSeekJudge(
        raw_log_path=raw_path, runs_per_scenario=3, model=MODEL_JUDGE,
    )
    print(f"  Judge model: {judge.model}")

    dimensions = {}
    all_results = []

    for name, runner in [("D3_llm", lambda: run_d3_llm(judge)),
                          ("D4", lambda: run_d4(judge))]:
        print(f"  Running {name}...", end=" ", flush=True)
        result = runner()
        dimensions[name] = result
        all_results.extend(result["results"])
        status = "PASS" if result["passed"] else "FAIL"
        print(f"{status} ({result['correct']}/{result['total']})")

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    t2_result = {
        "test": "T2",
        "timestamp": datetime.now().isoformat(),
        "scenario_count": total,
        "correct": correct,
        "passed": all(d["passed"] for d in dimensions.values()),
        "dimensions": dimensions,
        "judge_info": {
            "model": judge.model,
            "api_calls": judge._call_count,
            "runs_per_scenario": judge.runs,
        },
    }
    _write_json(ROUND10_DIR / "results_t2.json", t2_result)
    print(f"\n  T2 total: {correct}/{total} — {'PASS' if t2_result['passed'] else 'FAIL'}")
    print(f"  Saved to {ROUND10_DIR / 'results_t2.json'}")
    return t2_result


def merge_and_report() -> None:
    """Combine T1 + T2 results into REPORT.md."""
    t1_path = ROUND10_DIR / "results_t1.json"
    t2_path = ROUND10_DIR / "results_t2.json"

    t1 = json.loads(t1_path.read_text(encoding="utf-8")) if t1_path.exists() else None
    t2 = json.loads(t2_path.read_text(encoding="utf-8")) if t2_path.exists() else None

    lines = [
        "# Engram Round 10: Retrieval/Injection Quality — Report",
        f"\n**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Summary
    total_cases = 0
    total_correct = 0
    all_pass = True

    if t1:
        total_cases += t1["scenario_count"]
        total_correct += t1["correct"]
        if not t1["passed"]:
            all_pass = False
    if t2:
        total_cases += t2["scenario_count"]
        total_correct += t2["correct"]
        if not t2["passed"]:
            all_pass = False

    lines.append("## 总结")
    lines.append(f"- 总 case 数: {total_cases}")
    lines.append(f"- 通过: {total_correct}")
    lines.append(f"- 失败: {total_cases - total_correct}")
    lines.append(f"- 通过率: {total_correct/total_cases*100:.1f}%" if total_cases else "- 通过率: N/A")
    lines.append(f"- **结果: {'PASS' if all_pass else 'FAIL'}**")
    lines.append("")

    # Dimension table
    lines.append("## 各维度结果")
    lines.append("")
    lines.append("| 维度 | 名称 | Case 数 | 通过 | 失败 | 门槛 | 状态 |")
    lines.append("|------|------|---------|------|------|------|------|")

    thresholds = {
        "D1": "8/8 (100%)",
        "D2": "4/6 (gate)",
        "D3_det": "7/8 (87.5%)",
        "D5": "5/7 (gate)",
        "D6": "7/8 (87.5%)",
        "D3_llm": ">=75%",
        "D4": "4/5 (80%)",
    }

    for source in [t1, t2]:
        if not source:
            continue
        for dim_key, dim in source.get("dimensions", {}).items():
            name = dim.get("name", dim_key)
            total = dim["total"]
            correct = dim["correct"]
            failed = total - correct
            threshold = thresholds.get(dim_key, "?")
            status = "PASS" if dim["passed"] else "FAIL"
            lines.append(f"| {dim_key} | {name} | {total} | {correct} | {failed} | {threshold} | {status} |")

    lines.append("")

    # Failed cases
    all_failures = []
    for source in [t1, t2]:
        if not source:
            continue
        for dim in source.get("dimensions", {}).values():
            for r in dim.get("results", []):
                if not r["correct"] and not r.get("known_issue"):
                    all_failures.append(r)

    if all_failures:
        lines.append("## 失败详情")
        lines.append("")
        for f in all_failures:
            lines.append(f"- **{f['id']}**: {f.get('detail', 'no detail')}")
        lines.append("")

    # Known issues
    known = []
    for source in [t1, t2]:
        if not source:
            continue
        for dim in source.get("dimensions", {}).values():
            for r in dim.get("results", []):
                if r.get("known_issue"):
                    known.append(r)

    if known:
        lines.append("## 已知问题（预期失败）")
        lines.append("")
        for k in known:
            lines.append(f"- **{k['id']}**: {k.get('detail', '')} → 冲突检测尚未实现")
        lines.append("")

    # LLM info
    if t2 and t2.get("judge_info"):
        ji = t2["judge_info"]
        lines.append("## LLM 评估信息")
        lines.append(f"- 模型: {ji.get('model', '?')}")
        lines.append(f"- API 调用次数: {ji.get('api_calls', '?')}")
        lines.append(f"- 每场景重复次数: {ji.get('runs_per_scenario', '?')}")
        lines.append("")

    # Conclusion
    lines.append("## 结论")
    if all_pass:
        lines.append("所有 HARD GATE 和 SOFT GATE 均达标。Retrieval/Injection 质量基线已建立。")
    else:
        lines.append("存在未通过的门槛，需要修复后重测。")

    report = "\n".join(lines) + "\n"
    report_path = ROUND10_DIR / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n  Report saved to {report_path}")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Round 10 retrieval quality benchmark")
    p.add_argument("--group", choices=["t1", "t2"], default=None,
                    help="Run only T1 (deterministic) or T2 (LLM)")
    p.add_argument("--merge-only", action="store_true",
                    help="Only merge existing results into REPORT.md")
    return p.parse_args()


if __name__ == "__main__":
    main()
