"""Run a small pytest target the way CI sees the repository.

This guard catches tests that only pass because the developer shell already
has ``PYTHONPATH=src`` or because pytest is launched from a forgiving working
directory. It runs selected tests from a temporary directory outside the repo
and removes ``PYTHONPATH`` from the subprocess environment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TARGETS = ("tests/test_cross_tool_resume_benchmark.py",)


def repo_root(start: Path | None = None) -> Path:
    """Return the repository root for this script."""
    anchor = Path(start or __file__).resolve()
    return anchor.parent.parent


def resolve_targets(root: Path, targets: list[str] | tuple[str, ...] | None) -> list[Path]:
    """Resolve target paths and require each one to stay inside ``root``."""
    root = root.resolve()
    resolved: list[Path] = []
    for target in targets or DEFAULT_TARGETS:
        raw = Path(target).expanduser()
        path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"pytest target is outside repository: {target}") from exc
        resolved.append(path)
    return resolved


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a subprocess env that does not inherit local PYTHONPATH hacks."""
    clean = dict(os.environ if env is None else env)
    clean.pop("PYTHONPATH", None)
    clean["PYTHONIOENCODING"] = "utf-8"
    return clean


def build_pytest_command(
    root: Path,
    targets: list[str] | tuple[str, ...] | None = None,
    *,
    python_executable: str | None = None,
) -> list[str]:
    """Build the pytest command using absolute target paths."""
    executable = python_executable or sys.executable
    return [
        executable,
        "-m",
        "pytest",
        *[str(path) for path in resolve_targets(root, targets)],
        "-q",
    ]


def run_ci_pytest_entrypoint(
    targets: list[str] | tuple[str, ...] | None = None,
    *,
    root: Path | None = None,
) -> int:
    """Run pytest from outside the repository and return its exit code."""
    root = (root or repo_root()).resolve()
    cmd = build_pytest_command(root, targets)
    with tempfile.TemporaryDirectory(prefix="engram-ci-pytest-") as tmp:
        result = subprocess.run(
            cmd,
            cwd=tmp,
            env=clean_env(),
            text=True,
        )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run selected tests from outside the repo without PYTHONPATH.",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="pytest target paths relative to the repository root",
    )
    args = parser.parse_args(argv)
    return run_ci_pytest_entrypoint(args.targets or None)


if __name__ == "__main__":
    raise SystemExit(main())
