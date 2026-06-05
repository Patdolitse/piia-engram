"""Run pytest targets the way CI sees repository imports.

This guard catches tests that only pass because the developer shell or pytest
rootdir put the repository root on ``sys.path``. CI installs the package, but
``scripts/`` is not a packaged import root; tests that import ``scripts.*`` must
therefore make that dependency explicit. The guard runs selected tests from a
temporary directory outside the repo with ``PYTHONPATH`` set only to ``ROOT/src``.
That keeps package imports available while excluding accidental repo-root
imports.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TARGETS = (
    "tests/test_admission_guard.py",
    "tests/test_memory_eval_suite.py",
    "tests/test_recall_eval.py",
    "tests/test_cross_tool_resume_benchmark.py",
)

_SCRIPT_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+scripts(?:\.|\s+import\b)|import\s+scripts(?:\.|\s|$))",
    re.MULTILINE,
)


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


def discover_script_import_tests(root: Path) -> tuple[str, ...]:
    """Return test files that directly import ``scripts.*`` modules."""
    root = root.resolve()
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return ()
    matches: list[str] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _SCRIPT_IMPORT_RE.search(text):
            matches.append(path.relative_to(root).as_posix())
    return tuple(matches)


def default_targets(root: Path, *, discover_script_imports: bool = False) -> tuple[str, ...]:
    """Return default targets, optionally expanded with direct script imports."""
    targets = list(DEFAULT_TARGETS)
    if discover_script_imports:
        targets.extend(discover_script_import_tests(root))
    return tuple(dict.fromkeys(targets))


def clean_env(root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a subprocess env that excludes repo root but keeps ``src`` imports."""
    clean = dict(os.environ if env is None else env)
    clean["PYTHONPATH"] = str((root.resolve() / "src").resolve())
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
    discover_script_imports: bool = False,
) -> int:
    """Run pytest from outside the repository and return its exit code."""
    root = (root or repo_root()).resolve()
    selected = tuple(targets) if targets else default_targets(
        root,
        discover_script_imports=discover_script_imports,
    )
    cmd = build_pytest_command(root, selected)
    with tempfile.TemporaryDirectory(prefix="engram-ci-pytest-") as tmp:
        result = subprocess.run(
            cmd,
            cwd=tmp,
            env=clean_env(root),
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
    parser.add_argument(
        "--discover-script-imports",
        action="store_true",
        help="Also run tests that directly import scripts.* modules.",
    )
    args = parser.parse_args(argv)
    return run_ci_pytest_entrypoint(
        args.targets or None,
        discover_script_imports=args.discover_script_imports,
    )


if __name__ == "__main__":
    raise SystemExit(main())
