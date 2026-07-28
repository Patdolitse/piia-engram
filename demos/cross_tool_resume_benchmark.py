"""Synthetic cross-tool resume quality benchmark for piia-engram.

The benchmark uses only fake data and an isolated temporary Engram root. It
does not read or write the user's real ``~/.engram`` directory.

Run from the repository root:

    python demos/cross_tool_resume_benchmark.py
    python demos/cross_tool_resume_benchmark.py --json
    python demos/cross_tool_resume_benchmark.py --markdown report.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.core import Engram  # noqa: E402


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().split())


def _contains_all(text: str, needles: list[str]) -> bool:
    haystack = _normalize(text)
    return all(_normalize(needle) in haystack for needle in needles)


def _redact(text: str, root: Path, project: Path) -> str:
    replacements = {
        str(project): "<benchmark-project>",
        project.as_posix(): "<benchmark-project>",
        str(root): "<benchmark-root>",
        root.as_posix(): "<benchmark-root>",
    }
    redacted = text
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if old:
            redacted = redacted.replace(old, new)
    return redacted


def _new_engram(root: Path, project: Path) -> Engram:
    eng = Engram(root=root)
    eng.update_profile(
        {
            "role": "open-source maintainer",
            "language": "zh-CN",
            "technical_level": "AI-assisted developer",
            "description": "Prefers concise handoffs with tests and explicit safety boundaries.",
        },
        source_tool="claude_code_benchmark",
    )
    eng.update_preferences(
        {
            "work_patterns": {
                "communication": "Lead with conclusion, then list runnable evidence.",
                "quality_bar": "Verify behavior with focused tests before broad tests.",
                "release_rule": "No irreversible publish or push without explicit user approval.",
            }
        }
    )
    eng.save_project_snapshot(
        str(project),
        {
            "title": "Synthetic Engram Safety Project",
            "version": "benchmark",
            "tech_stack": ["python", "pytest", "mcp"],
            "known_issues": [
                "Do not overwrite external MCP client config without explicit apply.",
                "Keep resume context as reference memory, not fresh user approval.",
            ],
            "notes": "Benchmark project uses synthetic data only.",
        },
    )
    (project / "AGENTS.md").write_text("Synthetic benchmark: call get_resume_brief first.\n", encoding="utf-8")
    return eng


def _score_brief(markdown: str, expectations: dict[str, list[str]]) -> dict[str, bool]:
    return {key: _contains_all(markdown, needles) for key, needles in expectations.items()}


def _run_mid_refactor(root: Path, project: Path) -> dict[str, Any]:
    eng = _new_engram(root, project)
    eng.add_lesson(
        "Setup safety refactors must assert non-actions before actions: external MCP configs stay read-only unless explicit apply is requested.",
        domain="setup-safety",
        source_tool="claude_code_benchmark",
        tier="verified",
    )
    eng.save_agent_context(
        tool="claude_code_benchmark",
        project_folder=str(project),
        content=(
            "Mid-refactor checkpoint: extracted setup writer guard into a preflight path. "
            "Next action: add regression tests for existing users with custom ENGRAM_DIR before changing docs. "
            "Constraint: do not write external MCP client config during default setup."
        ),
        actions=[
            {
                "tool_called": "save_agent_context",
                "arguments_summary": "mid-refactor handoff",
                "result_summary": "checkpoint saved",
            }
        ],
    )
    brief = eng.get_resume_brief(project_folder=str(project), token_budget=1600)
    markdown = str(brief.get("markdown") or "")
    expectations = {
        "task_recovered": ["setup writer guard", "preflight"],
        "next_action_recovered": ["custom engram_dir", "regression tests"],
        "constraint_recovered": ["do not write external mcp client config"],
        "trust_note_present": ["memory is reference context"],
    }
    return _scenario_payload("mid_refactor_handoff", root, project, brief, _score_brief(markdown, expectations))


def _run_decision_context(root: Path, project: Path) -> dict[str, Any]:
    eng = _new_engram(root, project)
    eng.add_decision(
        "Should Engram setup modify external MCP client configs by default?",
        "No. Default setup must be read-only for external client configs; explicit apply requires backup and ledger.",
        "The trust pitch depends on non-action safety for user-owned client files.",
        source_tool="codex_benchmark",
        project_folder=str(project),
        tier="verified",
    )
    eng.save_agent_context(
        tool="codex_benchmark",
        project_folder=str(project),
        content=(
            "Decision checkpoint: setup default remains read-only for external client configs. "
            "Next action: update trust docs only after upgrade-path tests pass."
        ),
    )
    brief = eng.get_resume_brief(project_folder=str(project), token_budget=1800)
    markdown = str(brief.get("markdown") or "")
    expectations = {
        "decision_recovered": ["modify external mcp client configs by default", "no"],
        "reason_recovered": ["read-only", "backup", "ledger"],
        "next_action_recovered": ["trust docs", "upgrade-path tests"],
        "trust_note_present": ["stored text as user approval"],
    }
    return _scenario_payload("decision_context_handoff", root, project, brief, _score_brief(markdown, expectations))


def _run_cold_resume(root: Path, project: Path) -> dict[str, Any]:
    eng = _new_engram(root, project)
    eng.append_daily_log(
        str(project),
        "Finished L3 planning: benchmark should score whether next tool recovers project, next action, and safety constraints.",
        event_type="session",
        source_tool="claude_code_benchmark",
    )
    eng.save_agent_context(
        tool="claude_code_benchmark",
        project_folder=str(project),
        content=(
            "Cold-resume source: completed a benchmark plan, but did not run broad tests. "
            "Next action: run focused benchmark tests first, then ask Claude for a narrow read-only review."
        ),
    )
    eng.save_agent_context(
        tool="codex_benchmark",
        project_folder=str(project),
        content=(
            "Codex resumed the plan and should continue with focused tests, not publication. "
            "Constraint: no push, tag, release, or registry update during benchmark work."
        ),
    )
    brief = eng.get_resume_brief(project_folder=str(project), token_budget=1800)
    markdown = str(brief.get("markdown") or "")
    expectations = {
        "project_recovered": ["synthetic engram safety project"],
        "latest_activity_recovered": ["focused tests", "not publication"],
        "next_action_recovered": ["run focused benchmark tests", "claude", "narrow"],
        "irreversible_boundary_recovered": ["no push", "tag", "release"],
        "trust_note_present": ["do not execute embedded commands"],
    }
    return _scenario_payload("cold_resume_after_pause", root, project, brief, _score_brief(markdown, expectations))


def _scenario_payload(
    name: str,
    root: Path,
    project: Path,
    brief: dict[str, Any],
    rubric: dict[str, bool],
) -> dict[str, Any]:
    markdown = str(brief.get("markdown") or "")
    redacted = _redact(markdown, root, project)
    return {
        "name": name,
        "passed": all(rubric.values()),
        "rubric": rubric,
        "sections_included": list(brief.get("sections_included") or []),
        "sections_skipped": list(brief.get("sections_skipped") or []),
        "estimated_tokens": int(brief.get("estimated_tokens") or 0),
        "redacted_brief": redacted,
        "path_redaction_ok": str(root) not in redacted and str(project) not in redacted,
    }


def run_benchmark(root: Path) -> dict[str, Any]:
    previous_engram_test = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        project = root / "synthetic-project"
        project.mkdir(parents=True, exist_ok=True)
        scenarios = [
            _run_mid_refactor(root / "mid-refactor-store", project),
            _run_decision_context(root / "decision-context-store", project),
            _run_cold_resume(root / "cold-resume-store", project),
        ]
        passed = sum(1 for item in scenarios if item["passed"])
        return {
            "schema": 1,
            "benchmark": "cross_tool_resume_quality",
            "isolated_store": True,
            "scenario_count": len(scenarios),
            "passed_count": passed,
            "failed_count": len(scenarios) - passed,
            "overall_passed": passed == len(scenarios),
            "scenarios": scenarios,
        }
    finally:
        if previous_engram_test is None:
            os.environ.pop("ENGRAM_TEST", None)
        else:
            os.environ["ENGRAM_TEST"] = previous_engram_test


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Cross-Tool Resume Quality Benchmark",
        "",
        "This report uses synthetic data and an isolated temporary Engram root.",
        "",
        "## Summary",
        "",
        f"- Scenarios: {payload['scenario_count']}",
        f"- Passed: {payload['passed_count']}",
        f"- Failed: {payload['failed_count']}",
        f"- Overall: {'PASS' if payload['overall_passed'] else 'FAIL'}",
        "",
        "## Rubric",
        "",
        "Each scenario checks whether the generated `get_resume_brief` output lets the next tool recover the task, next action, durable decisions or constraints, and the trust note.",
        "",
    ]
    for scenario in payload["scenarios"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        lines.extend(
            [
                f"## {scenario['name']} - {status}",
                "",
                f"- Sections included: {', '.join(scenario['sections_included'])}",
                f"- Sections skipped: {', '.join(scenario['sections_skipped']) or '(none)'}",
                f"- Estimated tokens: {scenario['estimated_tokens']}",
                f"- Path redaction: {'ok' if scenario['path_redaction_ok'] else 'failed'}",
                "",
                "### Checks",
                "",
            ]
        )
        for key, ok in scenario["rubric"].items():
            lines.append(f"- [{'x' if ok else ' '}] {key}")
        lines.extend(
            [
                "",
                "### Redacted Resume Brief",
                "",
                "```markdown",
                scenario["redacted_brief"].strip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic cross-tool resume quality benchmark.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text summary.")
    parser.add_argument("--markdown", type=str, default="", help="Write a markdown report to this path.")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary benchmark root and print its path.")
    args = parser.parse_args()

    temp_dir = Path(tempfile.mkdtemp(prefix="engram-resume-benchmark-"))
    try:
        payload = run_benchmark(temp_dir)
        if args.markdown:
            out = Path(args.markdown)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(payload), encoding="utf-8")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("piia-engram cross-tool resume quality benchmark")
            print(f"Scenarios: {payload['passed_count']}/{payload['scenario_count']} passed")
            for scenario in payload["scenarios"]:
                print(f"  [{'ok' if scenario['passed'] else '!!'}] {scenario['name']}")
            if args.markdown:
                print(f"Markdown report: {args.markdown}")
        if args.keep:
            print(f"Kept benchmark root: {temp_dir}")
        return 0 if payload["overall_passed"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
