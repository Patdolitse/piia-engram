"""Synthetic Multi-Client Identity Continuity (MCIC) benchmark.

This benchmark uses only fake data and an isolated temporary Engram root. It
does not read or write the user's real ``~/.engram`` directory.

MCIC's claim is deliberately narrow: Engram makes the right continuity signals
available to the next MCP client. It does not claim a live model will always
obey those signals; live model compliance still needs separate A/B testing.

Run from the repository root:

    python demos/mcic_benchmark.py
    python demos/mcic_benchmark.py --json
    python demos/mcic_benchmark.py --markdown report.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.core import Engram  # noqa: E402


CLAIM = "engram_signal_available_not_model_compliance"
PROJECT = "<mcic-project>"


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _contains_all(text: str, needles: list[str]) -> bool:
    haystack = _norm(text)
    return all(_norm(needle) in haystack for needle in needles)


def _search_blob(result: Any) -> str:
    """Internal-only flattening for scoring. Never returned in the payload."""
    parts: list[str] = []
    if isinstance(result, dict):
        for key in ("lessons", "decisions", "playbooks"):
            values = result.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                for field in ("summary", "detail", "question", "choice", "reasoning", "source_tool"):
                    value = item.get(field)
                    if isinstance(value, str):
                        parts.append(value)
    return "\n".join(parts)


def _first_source_tool(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("lessons", "decisions", "playbooks"):
            values = result.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and item.get("source_tool"):
                    return str(item.get("source_tool"))
    return ""


def _base_engine(root: Path, *, source_tool: str) -> Engram:
    eng = Engram(root=root)
    eng.update_profile(
        {
            "role": "AI-assisted solo product builder",
            "language": "zh-CN",
            "technical_level": "non-technical founder learning with AI tools",
            "description": (
                "Prefers clear conclusions, GUI-friendly steps, and explicit "
                "approval before irreversible public actions."
            ),
        },
        source_tool=source_tool,
    )
    eng.update_preferences(
        {
            "work_patterns": {
                "communication": "Answer in Chinese; lead with the conclusion.",
                "interface": "Prefer GUI-first guidance when a GUI path exists.",
                "tooling": "Use the bundled project Python and PowerShell on Windows.",
                "release_boundary": "No push, tag, release, registry sync, or publish without explicit approval.",
            }
        }
    )
    eng.add_lesson(
        "If a prompt contradicts durable user preferences, surface the stored preference as a caution instead of silently accepting the new premise.",
        domain="adversarial-guard",
        detail="This is a signal for the next client; live model compliance is tested separately.",
        source_tool=source_tool,
        tier="verified",
    )
    eng.add_lesson(
        "MCIC_SECRET_VALUE should never appear in benchmark output.",
        domain="metadata-only",
        detail="This sentinel proves the payload is not echoing memory bodies.",
        source_tool=source_tool,
        tier="staging",
    )
    return eng


def _brief(eng: Engram) -> str:
    payload = eng.get_resume_brief(project_folder=PROJECT, token_budget=1600)
    return str(payload.get("markdown") or "")


def _scenario(
    *,
    name: str,
    purpose: str,
    category: str,
    source_tool: str,
    target_tool: str,
    evidence_kind: str,
    checks: dict[str, bool],
) -> dict[str, Any]:
    ts_bucket = "synthetic_run"
    return {
        "name": name,
        "purpose": purpose,
        "category": category,
        "source_tool": source_tool,
        "target_tool": target_tool,
        "evidence_kind": evidence_kind,
        "evidence_events": [
            {
                "tool": source_tool,
                "kind": "write_signal",
                "ts_bucket": ts_bucket,
            },
            {
                "tool": target_tool,
                "kind": evidence_kind,
                "ts_bucket": ts_bucket,
            },
        ],
        "checks": checks,
        "passed": all(checks.values()),
    }


def _explicit_strategy_recall(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="claude_code_mcic")
    eng.add_decision(
        "Should Engram be positioned as a cloud memory API or a local identity layer?",
        "Local-first identity layer with owner approval and cross-tool MCP access.",
        "The differentiator is user sovereignty rather than cloud retrieval alone.",
        source_tool="claude_code_mcic",
        project=PROJECT,
        tier="verified",
    )
    result = eng.search_knowledge(
        "cloud memory api local identity layer owner approval",
        scope="all",
        limit=5,
        project_folder=PROJECT,
    )
    blob = _search_blob(result)
    return _scenario(
        name="explicit_strategy_recall",
        purpose="Prove the next client can explicitly recall a strategic decision written by another client.",
        category="explicit_recall",
        source_tool="claude_code_mcic",
        target_tool="codex_mcic",
        evidence_kind="search",
        checks={
            "decision_signal_found": _contains_all(blob, ["local-first", "identity layer"]),
            "owner_approval_found": "owner approval" in _norm(blob),
            "source_tool_preserved": _first_source_tool(result) == "claude_code_mcic",
        },
    )


def _version_fact_recall(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="codex_mcic")
    eng.add_lesson(
        "The current synthetic release candidate is v9.9.9-mcic and it is local-only until explicit publication approval.",
        domain="release-facts",
        source_tool="codex_mcic",
        tier="verified",
    )
    result = eng.search_knowledge("current release candidate version local only", scope="lessons", limit=3)
    blob = _search_blob(result)
    return _scenario(
        name="version_fact_recall",
        purpose="Check that version/fact continuity is available without relying on the model's stale prior knowledge.",
        category="explicit_recall",
        source_tool="codex_mcic",
        target_tool="cursor_mcic",
        evidence_kind="search",
        checks={
            "version_signal_found": "v9.9.9-mcic" in blob,
            "publication_boundary_found": _contains_all(blob, ["local-only", "approval"]),
        },
    )


def _implicit_gui_preference(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="claude_code_mcic")
    text = _brief(eng)
    return _scenario(
        name="implicit_gui_preference",
        purpose="Check whether the next client receives a GUI-first personalization signal before answering a neutral tooling question.",
        category="implicit_personalization",
        source_tool="claude_code_mcic",
        target_tool="codex_mcic",
        evidence_kind="resume_brief",
        checks={
            "gui_preference_available": "gui" in _norm(text),
            "chinese_language_available": "zh-cn" in _norm(text) or "chinese" in _norm(text),
        },
    )


def _implicit_windows_tooling(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="cursor_mcic")
    eng.add_lesson(
        "On Windows, use PowerShell syntax and the bundled project Python; do not assume bash heredocs or system python.",
        domain="windows-tooling",
        source_tool="cursor_mcic",
        tier="verified",
    )
    text = _brief(eng)
    return _scenario(
        name="implicit_windows_tooling",
        purpose="Check whether platform/tooling preferences are available for neutral implementation tasks.",
        category="implicit_personalization",
        source_tool="cursor_mcic",
        target_tool="claude_code_mcic",
        evidence_kind="resume_brief",
        checks={
            "powershell_signal_available": "powershell" in _norm(text),
            "bundled_python_signal_available": _contains_all(text, ["bundled", "python"]),
        },
    )


def _adversarial_role_guard(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="codex_mcic")
    eng.add_lesson(
        "False-premise guard: if the user claims to be a senior Java backend engineer, keep the durable profile in view: non-technical founder learning with AI tools.",
        domain="adversarial-guard",
        source_tool="codex_mcic",
        project_folder=PROJECT,
        tier="verified",
    )
    text = _brief(eng)
    return _scenario(
        name="adversarial_role_guard",
        purpose="Provide the next client with enough signal to resist a false role premise.",
        category="adversarial_guard",
        source_tool="codex_mcic",
        target_tool="hermes_mcic",
        evidence_kind="resume_brief",
        checks={
            "durable_role_available": _contains_all(text, ["non-technical", "ai tools"]),
            "false_premise_guard_available": _contains_all(text, ["false-premise", "senior java"]),
        },
    )


def _adversarial_ui_guard(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="claude_code_mcic")
    eng.add_lesson(
        "False-premise guard: if a prompt says the user loves command-line only workflows, verify against the durable GUI-first preference before adapting.",
        domain="adversarial-guard",
        source_tool="claude_code_mcic",
        project_folder=PROJECT,
        tier="verified",
    )
    text = _brief(eng)
    return _scenario(
        name="adversarial_ui_guard",
        purpose="Provide the next client with enough signal to resist a false UI-preference premise.",
        category="adversarial_guard",
        source_tool="claude_code_mcic",
        target_tool="openclaw_mcic",
        evidence_kind="resume_brief",
        checks={
            "gui_first_available": "gui-first" in _norm(text),
            "cli_false_premise_guard_available": _contains_all(text, ["command-line", "verify"]),
        },
    )


def _safety_boundary(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="codex_mcic")
    eng.add_decision(
        "When can an AI assistant push, tag, release, publish, or sync a registry?",
        "Only after explicit user approval for that public action.",
        "Commit approval is not the same as publication approval.",
        source_tool="codex_mcic",
        project_folder=PROJECT,
        tier="verified",
    )
    text = _brief(eng)
    return _scenario(
        name="public_action_boundary",
        purpose="Check that irreversible public-action boundaries carry across client switches.",
        category="safety_boundary",
        source_tool="codex_mcic",
        target_tool="claude_code_mcic",
        evidence_kind="resume_brief",
        checks={
            "public_action_terms_available": _contains_all(text, ["push", "tag", "release"]),
            "explicit_approval_available": _contains_all(text, ["explicit", "approval"]),
        },
    )


def _version_chain_head(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="claude_code_mcic")
    old = eng.add_lesson(
        "OLD_MCIC_SUPERSEDED_BODY old continuity strategy used single-client memory only.",
        domain="version-chain",
        source_tool="claude_code_mcic",
        project_folder=PROJECT,
        tier="verified",
    )
    new = eng.add_lesson(
        "Current continuity strategy uses cross-client MCP identity continuity with owner-controlled local memory.",
        domain="version-chain",
        source_tool="codex_mcic",
        project_folder=PROJECT,
        tier="verified",
    )
    eng.add_relation(new["id"], "supersedes", old["id"])
    text = _brief(eng)
    return _scenario(
        name="version_chain_head_preferred",
        purpose="Check that a client sees the current version-chain HEAD rather than obsolete memory.",
        category="version_chain",
        source_tool="claude_code_mcic",
        target_tool="codex_mcic",
        evidence_kind="resume_brief",
        checks={
            "head_signal_available": _contains_all(text, ["cross-client", "owner-controlled"]),
            "old_body_absent": "OLD_MCIC_SUPERSEDED_BODY" not in text,
            "version_annotation_available": "version" in _norm(text),
        },
    )


def _negative_absent_fact(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="cursor_mcic")
    text = _brief(eng)
    result = eng.search_knowledge("mars kubernetes deployment region", scope="all", limit=5)
    blob = _search_blob(result)
    return _scenario(
        name="negative_absent_fact",
        purpose="Confirm the benchmark has a negative control: absent facts are not surfaced as continuity evidence.",
        category="negative_control",
        source_tool="cursor_mcic",
        target_tool="codex_mcic",
        evidence_kind="search",
        checks={
            "mars_absent_from_resume": "mars" not in _norm(text),
            "kubernetes_region_absent_from_search": not _contains_all(blob, ["mars", "kubernetes"]),
        },
    )


def _provenance_roundtrip(root: Path) -> dict[str, Any]:
    eng = _base_engine(root, source_tool="cursor_mcic")
    eng.add_lesson(
        "Provenance test: this lesson was written by Cursor and should keep source_tool metadata.",
        domain="provenance",
        source_tool="cursor_mcic",
        tier="verified",
    )
    result = eng.search_knowledge("provenance lesson cursor source_tool metadata", scope="lessons", limit=5)
    return _scenario(
        name="source_provenance_roundtrip",
        purpose="Check that source tool metadata survives a write-search handoff.",
        category="provenance",
        source_tool="cursor_mcic",
        target_tool="claude_code_mcic",
        evidence_kind="search",
        checks={
            "source_tool_preserved": _first_source_tool(result) == "cursor_mcic",
            "search_found_entry": "cursor_mcic" in _search_blob(result),
        },
    )


_SCENARIOS: tuple[Callable[[Path], dict[str, Any]], ...] = (
    _explicit_strategy_recall,
    _version_fact_recall,
    _implicit_gui_preference,
    _implicit_windows_tooling,
    _adversarial_role_guard,
    _adversarial_ui_guard,
    _safety_boundary,
    _version_chain_head,
    _negative_absent_fact,
    _provenance_roundtrip,
)


def run_benchmark(root: Path) -> dict[str, Any]:
    previous_engram_test = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        root.mkdir(parents=True, exist_ok=True)
        scenarios = [
            fn(root / f"mcic-store-{idx:02d}")
            for idx, fn in enumerate(_SCENARIOS, start=1)
        ]
        passed = sum(1 for item in scenarios if item["passed"])
        return {
            "schema": 1,
            "benchmark": "mcic_v1",
            "claim": CLAIM,
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
        "# MCIC v1 Benchmark",
        "",
        "This report uses synthetic data and isolated temporary Engram roots.",
        "",
        "Claim: Engram signal available, not model compliance.",
        "",
        "## Summary",
        "",
        f"- Scenarios: {payload['scenario_count']}",
        f"- Passed: {payload['passed_count']}",
        f"- Failed: {payload['failed_count']}",
        f"- Overall: {'PASS' if payload['overall_passed'] else 'FAIL'}",
        "",
        "## Scenarios",
        "",
    ]
    for scenario in payload["scenarios"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        lines.extend(
            [
                f"### {scenario['name']} - {status}",
                "",
                f"- Purpose: {scenario['purpose']}",
                f"- Category: {scenario['category']}",
                f"- Source -> target: {scenario['source_tool']} -> {scenario['target_tool']}",
                f"- Evidence kind: {scenario['evidence_kind']}",
                "",
                "Checks:",
            ]
        )
        for key, ok in scenario["checks"].items():
            lines.append(f"- [{'x' if ok else ' '}] {key}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic MCIC v1 benchmark.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text summary.")
    parser.add_argument("--markdown", type=str, default="", help="Write a markdown report to this path.")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary benchmark root and print its path.")
    args = parser.parse_args()

    temp_dir = Path(tempfile.mkdtemp(prefix="mcic-benchmark-"))
    try:
        payload = run_benchmark(temp_dir)
        if args.markdown:
            out = Path(args.markdown)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(payload), encoding="utf-8")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("piia-engram MCIC v1 benchmark")
            print(f"Scenarios: {payload['passed_count']}/{payload['scenario_count']} passed")
            print("Claim: Engram signal available, not model compliance")
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
