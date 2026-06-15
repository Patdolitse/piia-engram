"""Guard the public release-maintenance surface.

This is a local, read-only check for files that are easy to treat as harmless
release plumbing but are still public when tracked:

- release scripts and pre-release checklists;
- marker-only release evidence files;
- the publish allowlist itself.

It blocks maintainer-local paths and prevents gitignored detailed release notes
from becoming tracked/public by accident.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


RELEASE_SCRIPT_FILES = {
    "scripts/check_pre_push_release_readiness.py",
    "scripts/check_public_release_surface.py",
    "scripts/check_release_auth_preflight.py",
    "scripts/check_release_gate.py",
    "scripts/publish_mcp_registry.py",
    "scripts/publish_pypi_fallback.py",
    "scripts/release_orchestrator.py",
}

_PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|Temp|AppData|CodexData)\b"),
    re.compile(r"(?i)\b/[Uu]sers/[A-Za-z0-9._-]+/"),
    re.compile(r"(?i)\b/home/[A-Za-z0-9._-]+/"),
)
_LOCAL_REVIEW_LOG_RE = re.compile(
    r"(?i)\b(?:codex|claude)[_-]?review(?:[_-][A-Za-z0-9_.-]+)?\.log\b"
)
_MARKER_LINE_RE = re.compile(r"^\s*-\s*[A-Za-z][\w-]*\s*:\s*.+\s*$")


def _git_tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=root,
            text=True,
            encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"git ls-files failed: {exc}") from exc
    return [line.replace("\\", "/") for line in out.splitlines() if line.strip()]


def _hit(rel: str, code: str, detail: str, *, line: int | None = None) -> dict:
    out = {"file": rel, "code": code, "detail": detail}
    if line is not None:
        out["line"] = line
    return out


def _is_marker_evidence_file(rel: str) -> bool:
    name = Path(rel).name
    return (
        rel.startswith("release-evidence/")
        and name.startswith("v")
        and name.endswith(".md")
        and not name.endswith("-notes.md")
    )


def _is_release_surface_file(rel: str) -> bool:
    return (
        rel in RELEASE_SCRIPT_FILES
        or rel == ".publishallow"
        or rel.startswith("release-evidence/")
    )


def _allowed_marker_line(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or stripped.startswith("# Release evidence")
        or _MARKER_LINE_RE.match(stripped) is not None
        or stripped.startswith("Marker-only declaration.")
        or stripped.startswith("(release-evidence/*-notes.md,")
    )


def _scan_text(rel: str, text: str) -> list[dict]:
    hits: list[dict] = []
    marker_file = _is_marker_evidence_file(rel)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in _PRIVATE_PATH_PATTERNS:
            if pattern.search(line):
                hits.append(_hit(rel, "private_path", "private/local path", line=lineno))
                break
        if _LOCAL_REVIEW_LOG_RE.search(line):
            hits.append(
                _hit(rel, "local_review_log", "local review log name", line=lineno)
            )
        if marker_file and not _allowed_marker_line(line):
            hits.append(
                _hit(
                    rel,
                    "non_marker_evidence_line",
                    "release evidence must stay marker-only",
                    line=lineno,
                )
            )
    return hits


def scan_public_release_surface(
    root: str | Path,
    *,
    tracked_files: list[str] | None = None,
) -> list[dict]:
    root_path = Path(root)
    tracked = (
        [p.replace("\\", "/") for p in tracked_files]
        if tracked_files is not None
        else _git_tracked_files(root_path)
    )
    hits: list[dict] = []
    for rel in tracked:
        if rel.startswith("release-evidence/") and rel.endswith("-notes.md"):
            hits.append(
                _hit(rel, "tracked_release_notes", "detailed release notes are local-only")
            )
        if rel == ".publishallow":
            path = root_path / rel
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                if "release-evidence/v*.md" in text:
                    hits.append(
                        _hit(
                            rel,
                            "release_evidence_wildcard",
                            "enumerate marker files; do not allowlist notes by glob",
                        )
                    )
        if not _is_release_surface_file(rel):
            continue
        path = root_path / rel
        if path.is_file():
            hits.extend(_scan_text(rel, path.read_text(encoding="utf-8", errors="replace")))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="Repo root (default: cwd).")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    hits = scan_public_release_surface(root)
    if not hits:
        print("[OK] public release surface is clean.")
        return 0
    print("::error::public release surface has private/internal content:")
    for hit in hits:
        loc = f"{hit['file']}:{hit['line']}" if "line" in hit else hit["file"]
        print(f"  - {loc} {hit['code']}: {hit['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
