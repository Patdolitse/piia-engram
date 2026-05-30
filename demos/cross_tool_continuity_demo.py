"""Safe cross-tool continuity demo for piia-engram.

This demo does not read or write the user's real ``~/.engram`` store. It creates
an isolated temporary Engram root, simulates Claude Code writing a lesson and a
decision, then simulates Codex and Cursor reading the same local identity layer.

Run from the repository root:

    python demos/cross_tool_continuity_demo.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.core import Engram  # noqa: E402


DEMO_PROJECT = "<demo-project>"


def _print_step(title: str) -> None:
    print(f"\n== {title} ==")


def _seed_demo_store(root: Path) -> Engram:
    engram = Engram(root=root)

    engram.update_profile(
        {
            "role": "solo SaaS developer",
            "language": "zh-CN",
            "technical_level": "intermediate",
            "description": "Uses AI coding tools and wants concise, test-backed handoffs.",
        },
        source_tool="claude_code_demo",
    )
    engram.update_preferences(
        {
            "work_patterns": {
                "communication": "Lead with the conclusion, then give the smallest runnable next step.",
                "quality_bar": "Run relevant tests before changing public behavior.",
                "privacy": "Public screenshots must not include local paths, email addresses, or tokens.",
            }
        }
    )

    lesson = engram.add_lesson(
        "For payment webhooks, verify the signature before writing business state; failed events must be replayable.",
        domain="payments",
        detail=(
            "The handler should keep raw event metadata long enough to retry safely. "
            "Do not drop failed events silently."
        ),
        source_tool="claude_code_demo",
        tier="verified",
    )
    decision = engram.add_decision(
        "Should payment webhook side effects run inline or through a queue?",
        "Validate and persist synchronously, then process side effects in a background job.",
        (
            "This keeps webhook responses stable, makes provider retries safe, "
            "and gives later AI sessions a clear recovery point."
        ),
        source_tool="claude_code_demo",
        project=DEMO_PROJECT,
        tier="verified",
    )
    context = engram.save_agent_context(
        tool="claude_code_demo",
        project_folder=DEMO_PROJECT,
        content=(
            "Completed payment webhook refactor. Signature validation now happens "
            "before state changes. Next step: add retry tests before modifying handler logic."
        ),
        actions=[
            {
                "tool_called": "add_lesson",
                "arguments_summary": "domain=payments, source_tool=claude_code_demo",
                "result_summary": f"lesson_id={lesson.get('id', '<id>')}",
            },
            {
                "tool_called": "add_decision",
                "arguments_summary": "question=webhook side effects",
                "result_summary": f"decision_id={decision.get('id', '<id>')}",
            },
        ],
    )

    print("[Claude Code] wrote:")
    print(f"  lesson: {lesson.get('id', '<id>')} source_tool={lesson.get('source_tool')}")
    print(f"  decision: {decision.get('id', '<id>')} source_tool={decision.get('source_tool')}")
    print(f"  session: {context.get('session_id')} tool={context.get('tool')}")

    return engram


def _simulate_codex(engram: Engram) -> None:
    _print_step("Codex opens a new session")
    brief = engram.get_resume_brief(project_folder=DEMO_PROJECT, token_budget=650)
    markdown = brief.get("markdown", "")
    checks = [
        "solo SaaS developer" in markdown,
        "payment webhook" in markdown.lower(),
        "claude_code_demo" in markdown,
    ]
    print("[Codex] resume brief includes:")
    print(f"  identity: {'yes' if checks[0] else 'no'}")
    print(f"  recent payment context: {'yes' if checks[1] else 'no'}")
    print(f"  source provenance: {'yes' if checks[2] else 'no'}")
    print("  suggested next step: add retry tests before changing handler logic")


def _simulate_cursor(engram: Engram) -> None:
    _print_step("Cursor/Windsurf searches the same memory")
    result = engram.search_knowledge("payment webhooks signature replayable", scope="all", limit=5)
    matches = []
    if isinstance(result, dict):
        for key in ("lessons", "decisions", "playbooks"):
            values = result.get(key, [])
            if isinstance(values, list):
                matches.extend(values)
    first = matches[0] if matches else {}
    source_tool = first.get("source_tool", "<missing>")
    summary = first.get("summary") or first.get("question") or "<missing>"
    print("[Cursor] search_knowledge('payment webhooks signature replayable'):")
    print(f"  found: {'yes' if matches else 'no'}")
    print(f"  source_tool: {source_tool}")
    print(f"  summary: {summary[:100]}")


def run_demo(root: Path) -> None:
    print("piia-engram cross-tool continuity demo")
    print("Store: <demo-root> (isolated temporary data, not ~/.engram)")
    _print_step("Claude Code records the handoff")
    engram = _seed_demo_store(root)
    _simulate_codex(engram)
    _simulate_cursor(engram)
    _print_step("Result")
    print("The same local Engram store was written by a simulated Claude Code client")
    print("and read by simulated Codex and Cursor/Windsurf clients.")
    print("No real user identity, local path, token, or project name is required.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the safe cross-tool continuity demo.")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary demo store after the run and print its path.",
    )
    args = parser.parse_args()

    temp_dir = Path(tempfile.mkdtemp(prefix="engram-cross-tool-demo-"))
    previous_engram_test = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        run_demo(temp_dir)
        if args.keep:
            print(f"\nKept demo store: {temp_dir}")
        return 0
    finally:
        if previous_engram_test is None:
            os.environ.pop("ENGRAM_TEST", None)
        else:
            os.environ["ENGRAM_TEST"] = previous_engram_test
        if not args.keep:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
