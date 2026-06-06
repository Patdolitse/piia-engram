"""Dry-run the post-push workspace closeout checklist.

This script is intentionally dry-run only. It computes the status values that
AGENTS.md / CLAUDE.md require after a future owner-approved push, but it does
not edit PROJECT_REGISTRY.md, push commits, tag releases, call registries,
deploy Workers, or post public comments.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


def _run(cmd: list[str], root: Path, timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as exc:
        return 127, "", f"executable not found: {exc.filename}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def _pyproject_version(root: Path) -> str:
    path = root / "pyproject.toml"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _public_fact_tests(root: Path) -> str:
    path = root / "docs" / "public-facts.json"
    if not path.is_file():
        return "unknown"
    data = json.loads(path.read_text(encoding="utf-8"))
    facts = data.get("facts", {})
    return f"{facts.get('test_collected', 'unknown')} collected"


def collect_closeout_status(
    root: str | Path,
    *,
    query_stars: bool = False,
    run_collect: bool = False,
) -> dict:
    root_path = Path(root).resolve()
    workspace = root_path.parent
    version = _pyproject_version(root_path)
    latest_tag_rc, latest_tag, _ = _run(["git", "tag", "--sort=-creatordate"], root_path)
    latest_tag = latest_tag.splitlines()[0] if latest_tag_rc == 0 and latest_tag else ""

    if run_collect:
        rc, stdout, stderr = _run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            root_path,
            timeout=600,
        )
        test_count = stdout.splitlines()[-1] if rc == 0 and stdout else f"collect failed: {stderr or stdout}"
    else:
        test_count = _public_fact_tests(root_path)

    stars: str | int = "not-queried"
    if query_stars:
        rc, stdout, stderr = _run(
            ["gh", "repo", "view", "--json", "stargazerCount", "-q", ".stargazerCount"],
            root_path,
        )
        stars = int(stdout) if rc == 0 and stdout.isdigit() else f"unavailable: {stderr or stdout}"

    return {
        "schema_version": 1,
        "dry_run": True,
        "root": str(root_path),
        "workspace": str(workspace),
        "project_registry": str(workspace / "PROJECT_REGISTRY.md"),
        "auto_status": {
            "version": version,
            "latest_tag": latest_tag,
            "tests": test_count,
            "github_stars": stars,
            "last_updated": date.today().isoformat(),
        },
        "blocked_actions": [
            "no file writes",
            "no git push",
            "no tag",
            "no release",
            "no registry write",
            "no deploy",
            "no public comments",
        ],
    }


def render_text(status: dict) -> str:
    auto = status["auto_status"]
    return "\n".join([
        "Post-push closeout dry-run",
        f"PROJECT_REGISTRY: {status['project_registry']}",
        f"Version: {auto['version']}",
        f"Latest tag: {auto['latest_tag']}",
        f"Tests: {auto['tests']}",
        f"GitHub stars: {auto['github_stars']}",
        f"Last updated: {auto['last_updated']}",
        "",
        "No files were written and no public actions were performed.",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="Repo root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--query-stars", action="store_true", help="Query GitHub stars with gh (read-only)")
    parser.add_argument("--run-collect", action="store_true", help="Run pytest collect-only instead of reading public facts")
    args = parser.parse_args(argv)

    status = collect_closeout_status(
        args.root,
        query_stars=args.query_stars,
        run_collect=args.run_collect,
    )
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(render_text(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
