"""Static lint for publish.yml release-gate ordering.

The publish workflow runs on a fresh GitHub Actions runner. Any gate that
imports ``piia_engram`` must run after project dependencies are installed, or
the release can fail before reaching PyPI. This script is intentionally
dependency-free: it scans the workflow text for step order and fails if a known
project-script gate appears before the dependency install step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_WORKFLOW = Path(".github") / "workflows" / "publish.yml"
INSTALL_MARKERS = (
    "pip install -e .",
    'pip install -e ".[dev]"',
    "pip install -e '.[dev]'",
)
PROJECT_GATE_MARKERS = (
    "python scripts/check_export_redaction.py",
    "python scripts/check_generated_export_redaction.py",
    "python scripts/check_release_gate.py",
    "python scripts/release_sanitize_check.py",
    "python scripts/check_release_artifact_private_terms.py",
)


def check_publish_workflow_order(text: str) -> tuple[bool, list[str]]:
    """Return whether dependency install precedes project-script gates."""
    problems: list[str] = []
    install_positions = [text.find(marker) for marker in INSTALL_MARKERS]
    install_positions = [pos for pos in install_positions if pos >= 0]
    if not install_positions:
        return False, ["missing project dependency install step before publish gates"]

    first_install = min(install_positions)
    for marker in PROJECT_GATE_MARKERS:
        pos = text.find(marker)
        if pos >= 0 and pos < first_install:
            problems.append(f"project gate appears before dependency install: {marker}")
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    args = parser.parse_args(argv)

    path = Path(args.workflow)
    if not path.is_file():
        print(f"::error::workflow not found: {path}", file=sys.stderr)
        return 2
    ok, problems = check_publish_workflow_order(path.read_text(encoding="utf-8"))
    if ok:
        print(f"[OK] publish workflow installs project dependencies before release gates ({path}).")
        return 0
    print("::error::publish workflow order is unsafe:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
