"""Dry-run the post-push workspace closeout checklist.

This script is intentionally dry-run only. It computes the status values that
AGENTS.md / CLAUDE.md require after a future owner-approved push, but it does
not edit PROJECT_REGISTRY.md, push commits, tag releases, call registries,
deploy Workers, or post public comments.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

# Workflow names that mean "a release/publish actually happened". Matched
# case-insensitively against the GitHub Actions run name.
_RELEASE_WORKFLOW_RE = re.compile(r"publish|release", re.IGNORECASE)


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


def _fetch_pypi_version(package: str = "piia-engram", timeout: int = 15) -> str:
    """Read-only: return the latest version published on PyPI, or a marker."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed host)
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("info", {}).get("version", "unknown"))
    except Exception as exc:  # network/JSON errors are non-fatal for a status probe
        return f"unavailable: {exc}"


def collect_github_status(
    root: str | Path,
    *,
    package: str = "piia-engram",
    query_pypi: bool = True,
) -> dict:
    """Read-only post-push GitHub probe.

    Answers the three questions an owner asks after a push:
      1. Did this commit's CI/guards pass on GitHub?
      2. Did this push accidentally trigger a release/publish workflow?
      3. What is the current GitHub Release / PyPI version (vs. local HEAD)?

    Performs NO writes, NO push, NO tag, NO release. It only calls read-only
    `gh` subcommands and (optionally) reads the public PyPI JSON endpoint.
    """
    root_path = Path(root).resolve()

    head_rc, head_sha, _ = _run(["git", "rev-parse", "HEAD"], root_path)
    head_sha = head_sha.strip() if head_rc == 0 else ""

    # Workflow runs for this exact commit.
    workflows: list[dict] = []
    gh_error: str | None = None
    if head_sha:
        rc, stdout, stderr = _run(
            [
                "gh", "run", "list", "--commit", head_sha, "--limit", "30",
                "--json", "name,event,status,conclusion",
            ],
            root_path,
        )
        if rc == 0 and stdout:
            try:
                for run in json.loads(stdout):
                    workflows.append({
                        "name": run.get("name", ""),
                        "event": run.get("event", ""),
                        "status": run.get("status", ""),
                        "conclusion": run.get("conclusion", "") or "running",
                    })
            except json.JSONDecodeError as exc:
                gh_error = f"could not parse gh run list: {exc}"
        else:
            gh_error = stderr or stdout or "gh run list failed"

    release_runs = [w for w in workflows if _RELEASE_WORKFLOW_RE.search(w["name"])]
    release_triggered = bool(release_runs)
    failed = [
        w for w in workflows
        if w["status"] == "completed" and w["conclusion"] not in ("success", "skipped", "neutral")
    ]
    in_progress = [w for w in workflows if w["status"] != "completed"]

    # Latest GitHub Release (read-only).
    rc, stdout, stderr = _run(
        ["gh", "release", "list", "--limit", "1", "--json", "tagName,name,isLatest,publishedAt"],
        root_path,
    )
    latest_release: dict | str
    if rc == 0 and stdout:
        try:
            parsed = json.loads(stdout)
            latest_release = parsed[0] if parsed else "none"
        except json.JSONDecodeError as exc:
            latest_release = f"unavailable: {exc}"
    else:
        latest_release = f"unavailable: {stderr or stdout}"

    pypi_version = _fetch_pypi_version(package) if query_pypi else "not-queried"

    return {
        "schema_version": 1,
        "read_only": True,
        "root": str(root_path),
        "head_sha": head_sha,
        "head_short": head_sha[:7],
        "workflows_for_head": workflows,
        "gh_error": gh_error,
        "ci_in_progress": in_progress,
        "ci_failed": failed,
        "release_triggered": release_triggered,
        "release_runs": release_runs,
        "latest_release": latest_release,
        "pypi_version": pypi_version,
        "checked_at": date.today().isoformat(),
    }


def render_github_status(status: dict) -> str:
    lines = ["Post-push GitHub status probe (read-only)", f"HEAD: {status['head_short']}"]

    wf = status["workflows_for_head"]
    if status.get("gh_error"):
        lines.append(f"Workflows: could not query ({status['gh_error']})")
    elif not wf:
        lines.append("Workflows: none found for this commit yet (may not have registered)")
    else:
        for w in wf:
            mark = "running" if w["status"] != "completed" else w["conclusion"]
            lines.append(f"  - {w['name']} [{w['event']}]: {w['status']}/{mark}")

    if status["release_triggered"]:
        names = ", ".join(r["name"] for r in status["release_runs"])
        lines.append(f"RELEASE TRIGGERED BY THIS PUSH: yes -> {names}")
    else:
        lines.append("Release triggered by this push: NO (expected for a plain push/merge)")

    if status["ci_failed"]:
        names = ", ".join(w["name"] for w in status["ci_failed"])
        lines.append(f"Failed checks: {names}")
    elif status["ci_in_progress"]:
        names = ", ".join(w["name"] for w in status["ci_in_progress"])
        lines.append(f"Still running: {names}")
    elif wf:
        lines.append("All checks: green")

    rel = status["latest_release"]
    if isinstance(rel, dict):
        lines.append(f"Latest GitHub Release: {rel.get('tagName', '?')} (latest={rel.get('isLatest')})")
    else:
        lines.append(f"Latest GitHub Release: {rel}")
    lines.append(f"PyPI published version: {status['pypi_version']}")
    lines.append("")
    lines.append("No files were written and no public actions were performed.")
    return "\n".join(lines)


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
    parser.add_argument(
        "--github-status",
        action="store_true",
        help="Read-only GitHub probe: this commit's Actions, accidental-release detection, latest Release, PyPI version",
    )
    parser.add_argument("--no-pypi", action="store_true", help="With --github-status, skip the PyPI version lookup")
    args = parser.parse_args(argv)

    if args.github_status:
        status = collect_github_status(args.root, query_pypi=not args.no_pypi)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(render_github_status(status))
        return 0

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
