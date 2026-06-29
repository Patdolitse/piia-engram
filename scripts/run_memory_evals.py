"""Run the offline memory quality eval suite.

This wrapper runs the frozen recall/admission baselines plus held-out fixtures
and emits aggregate, metadata-only results suitable for CI gates and release
evidence. It does not read or write the user's live Engram store.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from scripts.check_admission import evaluate_fixture, load_fixture as load_admission_fixture  # noqa: E402
from scripts.eval_agent_context_pack import run_eval as run_agent_context_pack_eval  # noqa: E402
from scripts.eval_recall import evaluate_corpus, load_corpus  # noqa: E402


DEFAULT_RECALL_FIXTURES = (
    ROOT / "tests" / "fixtures" / "recall_eval_v1.json",
    ROOT / "tests" / "fixtures" / "recall_eval_heldout_v1.json",
)
DEFAULT_ADMISSION_FIXTURES = (
    ROOT / "tests" / "fixtures" / "admission_guard_v1.json",
    ROOT / "tests" / "fixtures" / "admission_guard_heldout_v1.json",
)


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _recall_summary(path: Path) -> dict[str, Any]:
    corpus = load_corpus(path)
    with tempfile.TemporaryDirectory(prefix="engram-memory-recall-") as workdir:
        summary = evaluate_corpus(corpus, Path(workdir))
    metrics = summary.get("metrics") or {}
    return {
        "fixture": _display_path(path),
        "benchmark": summary.get("benchmark"),
        "public_safe": bool(summary.get("public_safe")),
        "overall_passed": bool(summary.get("overall_passed")),
        "case_count": int(summary.get("case_count") or 0),
        "passed_count": int(summary.get("passed_count") or 0),
        "failed_count": int(summary.get("failed_count") or 0),
        "mean_precision_at_k": float(metrics.get("mean_precision_at_k") or 0.0),
        "mean_recall_at_k": float(metrics.get("mean_recall_at_k") or 0.0),
        "mean_mrr": float(metrics.get("mean_mrr") or 0.0),
        "forbidden_leak_rate": float(metrics.get("forbidden_leak_rate") or 0.0),
        "negative_false_positive_rate": float(metrics.get("negative_false_positive_rate") or 0.0),
    }


def _admission_summary(path: Path) -> dict[str, Any]:
    summary = evaluate_fixture(load_admission_fixture(path))
    return {
        "fixture": _display_path(path),
        "guard": summary.get("guard"),
        "public_safe": bool(summary.get("public_safe")),
        "overall_passed": bool(summary.get("overall_passed")),
        "candidate_count": int(summary.get("candidate_count") or 0),
        "failed_expectation_count": len(summary.get("failed_expectations") or []),
        "action_counts": dict(summary.get("action_counts") or {}),
    }


def _agent_context_pack_summary() -> dict[str, Any]:
    summary = run_agent_context_pack_eval()
    cases = list(summary.get("cases") or [])
    case_count = len(cases)
    passed_count = sum(1 for case in cases if case.get("passed"))
    failed_count = case_count - passed_count
    public_safe = case_count > 0 and all(
        (case.get("checks") or {}).get("no_forbidden_substrings") is True for case in cases
    )
    overall_passed = case_count > 0 and bool(summary.get("overall_passed")) and failed_count == 0
    return {
        "schema": summary.get("schema"),
        "public_safe": public_safe,
        "store_isolated": True,
        "overall_passed": overall_passed,
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
    }


def run_suite(
    recall_fixtures: list[str | Path] | None = None,
    admission_fixtures: list[str | Path] | None = None,
) -> dict[str, Any]:
    recall_paths = DEFAULT_RECALL_FIXTURES if recall_fixtures is None else recall_fixtures
    admission_paths = DEFAULT_ADMISSION_FIXTURES if admission_fixtures is None else admission_fixtures
    recalls = [_recall_summary(Path(path)) for path in recall_paths]
    admissions = [_admission_summary(Path(path)) for path in admission_paths]
    agent_context_pack = _agent_context_pack_summary()
    sections = [*recalls, *admissions, agent_context_pack]
    return {
        "schema": 1,
        "suite": "memory_eval_suite_v1",
        "public_safe": all(item["public_safe"] for item in sections),
        "overall_passed": all(item["overall_passed"] for item in sections),
        "recall": recalls,
        "admission": admissions,
        "agent_context_pack": agent_context_pack,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Memory Eval Suite v1",
        "",
        f"Overall: {'PASS' if summary.get('overall_passed') else 'FAIL'}",
        "",
        "## Recall",
        "",
        "| Benchmark | Cases | Recall@k | MRR | Forbidden leak | Negative FP |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary.get("recall") or []:
        lines.append(
            f"| {item.get('benchmark')} | {item.get('passed_count')}/{item.get('case_count')} | "
            f"{item.get('mean_recall_at_k', 0):.3f} | {item.get('mean_mrr', 0):.3f} | "
            f"{item.get('forbidden_leak_rate', 0):.3f} | "
            f"{item.get('negative_false_positive_rate', 0):.3f} |"
        )
    lines.extend([
        "",
        "## Admission",
        "",
        "| Guard | Candidates | Actions | Failed expectations |",
        "|---|---:|---|---:|",
    ])
    for item in summary.get("admission") or []:
        actions = ", ".join(f"{k}={v}" for k, v in (item.get("action_counts") or {}).items())
        lines.append(
            f"| {item.get('guard')} | {item.get('candidate_count')} | "
            f"{actions} | {item.get('failed_expectation_count')} |"
        )
    agent_context_pack = summary.get("agent_context_pack") or {}
    lines.extend([
        "",
        "## Agent Context Pack",
        "",
        "| Schema | Cases | Public safe | Store isolated |",
        "|---|---:|---:|---:|",
        (
            f"| {agent_context_pack.get('schema')} | "
            f"{agent_context_pack.get('passed_count')}/{agent_context_pack.get('case_count')} | "
            f"{agent_context_pack.get('public_safe')} | "
            f"{agent_context_pack.get('store_isolated')} |"
        ),
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--output", default="", help="Optional file path for the rendered JSON/Markdown output.")
    args = parser.parse_args(argv)

    summary = run_suite()
    rendered = (
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        if args.as_json
        else render_markdown(summary)
    )
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
