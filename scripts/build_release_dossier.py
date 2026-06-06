"""Build a local v.next release-readiness dossier.

This script is read-only. It does not push, tag, release, upload, write a
registry, deploy, or refresh external listings. By default it gathers local
metadata only; pass ``--run-readiness`` to embed the local readiness gate result.
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


def _load_public_facts(root: Path) -> dict:
    path = root / "docs" / "public-facts.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _tool_counts(root: Path) -> dict:
    rc, stdout, stderr = _run(
        [sys.executable, "scripts/count_mcp_tools.py", "--json"],
        root,
    )
    if rc != 0:
        return {"error": stderr or stdout, "returncode": rc}
    return json.loads(stdout)


def _git_lines(root: Path, args: list[str]) -> list[str]:
    rc, stdout, _stderr = _run(["git", *args], root)
    if rc != 0 or not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def build_dossier(
    root: str | Path,
    *,
    run_readiness: bool = False,
    full_tests: bool = False,
) -> dict:
    root_path = Path(root).resolve()
    latest_tag = (_git_lines(root_path, ["tag", "--sort=-creatordate"]) or [""])[0]
    head = (_git_lines(root_path, ["rev-parse", "--short", "HEAD"]) or [""])[0]
    commit_range = f"{latest_tag}..HEAD" if latest_tag else "HEAD"

    dossier = {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "root": str(root_path),
        "read_only": True,
        "public_actions_performed": [],
        "publication_boundary": (
            "local dossier only; owner confirmation is still required before "
            "push/tag/release/upload/registry/deploy/external listing actions"
        ),
        "git": {
            "head": head,
            "latest_tag": latest_tag,
            "local_commits_after_latest_tag": _git_lines(
                root_path,
                ["log", "--oneline", commit_range],
            ),
        },
        "public_facts": _load_public_facts(root_path),
        "mcp_tool_counts": _tool_counts(root_path),
    }

    if run_readiness:
        import importlib.util

        script = root_path / "scripts" / "check_pre_push_release_readiness.py"
        spec = importlib.util.spec_from_file_location("_readiness_gate", script)
        if spec is None or spec.loader is None:
            dossier["readiness"] = {"ok": False, "error": "readiness script not loadable"}
        else:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            dossier["readiness"] = mod.run_checks(root_path, full_tests=full_tests)

    return dossier


def render_markdown(dossier: dict) -> str:
    facts = dossier.get("public_facts", {}).get("facts", {})
    counts = dossier.get("mcp_tool_counts", {})
    readiness = dossier.get("readiness")
    lines = [
        "# Local v.next Release Dossier",
        "",
        "> Local planning artifact only. This is not a release announcement and",
        "> does not approve push, tag, release, upload, registry publish, deploy,",
        "> or external listing refresh.",
        "",
        f"- Generated: {dossier['generated_at']}",
        f"- HEAD: `{dossier['git']['head']}`",
        f"- Latest tag: `{dossier['git']['latest_tag']}`",
        f"- MCP tools: {counts.get('total')} total / {counts.get('core')} core / {counts.get('advanced')} advanced",
        f"- Tests in public facts: {facts.get('test_passed')} passed / {facts.get('test_skipped')} skipped / {facts.get('test_collected')} collected",
        "",
        "## Local Commits After Latest Tag",
        "",
    ]
    commits = dossier["git"]["local_commits_after_latest_tag"]
    lines.extend([f"- `{line}`" for line in commits] or ["- none"])
    if readiness is not None:
        lines.extend([
            "",
            "## Readiness Gate",
            "",
            f"- ok: `{bool(readiness.get('ok'))}`",
            f"- mode: `{'full' if readiness.get('full_tests') else 'lightweight'}`",
        ])
    lines.extend([
        "",
        "## Blocked Public Actions",
        "",
        "- git push",
        "- tag / GitHub Release",
        "- PyPI upload",
        "- MCP Registry publish",
        "- Wrangler deploy",
        "- external listing refresh or public comment",
    ])
    return "\n".join(lines)


def _print_safe(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only local v.next release dossier."
    )
    parser.add_argument("--root", default=".", help="Repo root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    parser.add_argument("--run-readiness", action="store_true", help="Embed the local readiness gate result")
    parser.add_argument("--full-tests", action="store_true", help="When running readiness, include pytest -q")
    args = parser.parse_args(argv)

    dossier = build_dossier(
        args.root,
        run_readiness=args.run_readiness,
        full_tests=args.full_tests,
    )
    if args.json:
        _print_safe(json.dumps(dossier, ensure_ascii=False, indent=2))
    else:
        _print_safe(render_markdown(dossier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
