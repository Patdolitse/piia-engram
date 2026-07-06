"""Evaluate whether project_resume_pack.v1 helps a new agent continue work.

The eval is synthetic-only and uses an isolated temporary Engram store. It does
not read or write the user's live Engram directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from piia_engram.core import Engram  # noqa: E402


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "resume_pack_eval_cases.json"


@contextmanager
def _isolated_eval_environment():
    previous = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ENGRAM_TEST", None)
        else:
            os.environ["ENGRAM_TEST"] = previous


def _contains_all(blob: str, expected: list[str]) -> bool:
    lowered = blob.lower()
    return all(str(item).lower() in lowered for item in expected)


def _has_forbidden_content(value: Any, forbidden: list[str]) -> bool:
    if isinstance(value, str):
        return any(item and item in value for item in forbidden)
    if isinstance(value, list):
        return any(_has_forbidden_content(item, forbidden) for item in value)
    if isinstance(value, dict):
        return any(
            key in forbidden or _has_forbidden_content(item, forbidden)
            for key, item in value.items()
        )
    return False


def load_cases(path: str | Path = DEFAULT_FIXTURE) -> list[dict[str, Any]]:
    fixture_path = Path(path)
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("resume-pack eval fixture must be a list")
    return data


def _seed_case(engram: Engram, case: dict[str, Any], project_folder: Path) -> None:
    project_folder.mkdir(parents=True, exist_ok=True)
    snapshot = case.get("project_snapshot")
    if isinstance(snapshot, dict):
        engram.save_project_snapshot(str(project_folder), snapshot)

    for lesson in case.get("lessons") or []:
        if not isinstance(lesson, dict):
            continue
        engram.add_lesson(
            str(lesson.get("summary") or ""),
            domain=str(lesson.get("domain") or "continuity"),
            tier=str(lesson.get("tier") or "verified"),
            project_folder=str(project_folder),
        )

    for index, session in enumerate(case.get("sessions") or [], 1):
        if not isinstance(session, dict):
            continue
        engram.save_agent_context(
            str(session.get("tool") or "codex"),
            str(session.get("content") or ""),
            session_id=str(session.get("session_id") or f"session-{index}"),
            project_folder=str(project_folder),
        )


def evaluate_pack(name: str, pack: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    focus_blob = json.dumps(pack.get("handoff") or {}, ensure_ascii=False)
    trusted_blob = json.dumps(pack.get("trusted_context") or [], ensure_ascii=False)
    review_blob = json.dumps(pack.get("review_needed") or [], ensure_ascii=False)
    forbidden = [str(item) for item in expected.get("forbidden_substrings") or []]

    checks = {
        "focus": _contains_all(
            focus_blob,
            [expected.get("current_focus_contains")]
            if expected.get("current_focus_contains")
            else [],
        ),
        "trusted": _contains_all(
            trusted_blob,
            [str(item) for item in expected.get("trusted_context_contains") or []],
        ),
        "review": _contains_all(
            review_blob,
            [str(item) for item in expected.get("review_needed_contains") or []],
        ),
        "no_forbidden_substrings": not _has_forbidden_content(pack, forbidden),
    }
    return {
        "name": name,
        "passed": all(checks.values()),
        "checks": checks,
    }


def run_eval(fixture_path: str | Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    cases = load_cases(fixture_path)
    results: list[dict[str, Any]] = []
    with _isolated_eval_environment():
        with tempfile.TemporaryDirectory(prefix="engram-resume-pack-eval-") as workdir:
            root = Path(workdir)
            for index, case in enumerate(cases, 1):
                name = str(case.get("name") or f"case_{index}")
                store = root / f"store-{index}"
                project = root / f"project-{index}"
                engram = Engram(root=store)
                _seed_case(engram, case, project)
                pack = engram.build_project_resume_pack(project_folder=str(project))
                results.append(evaluate_pack(name, pack, case.get("expected") or {}))
    return {
        "schema": "resume_pack_eval.v1",
        "overall_passed": all(item["passed"] for item in results),
        "cases": results,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Resume Pack Eval",
        "",
        f"Overall: {'PASS' if summary.get('overall_passed') else 'FAIL'}",
        "",
        "| Case | Focus | Trusted | Review | No forbidden substrings |",
        "|---|---:|---:|---:|---:|",
    ]
    for case in summary.get("cases") or []:
        checks = case.get("checks") or {}
        lines.append(
            f"| {case.get('name')} | {checks.get('focus')} | "
            f"{checks.get('trusted')} | {checks.get('review')} | "
            f"{checks.get('no_forbidden_substrings')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    args = parser.parse_args(argv)

    summary = run_eval(args.fixture)
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(summary), end="")
    return 0 if summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
