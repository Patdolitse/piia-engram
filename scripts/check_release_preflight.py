"""Structural release preflight — make the v4.10.0 failure mode impossible.

v4.10.0 was tagged and a GitHub release was created, but its PyPI publish was
blocked because ``release-evidence/v4.10.0.md`` was never committed. Because the
publish gate checks out the TAG's tree, that could not be fixed after the fact.

This script turns "is this commit safe to tag/release?" into a deterministic,
scriptable invariant that runs BEFORE the irreversible tag/release:

Default mode (always-on invariant, cheap):
    python scripts/check_release_preflight.py [--root .]
  → verifies the package version is identical across every version-bearing
    surface (the inverse of bump_version.py).

Tag mode (pre-tag gate):
    python scripts/check_release_preflight.py --tag vX.Y.Z
  → all of the above, PLUS:
    - the pyproject version is a final SemVer X.Y.Z and ``--tag`` == ``v`` + it,
    - the working tree is clean (so HEAD is exactly what will be tagged),
    - no local tag ``vX.Y.Z`` already exists,
    - ``release-evidence/vX.Y.Z.md`` exists IN THE HEAD TREE (not just on disk),
      is git-tracked, and is listed in ``.publishallow``,
    - ``check_release_gate.py --version X.Y.Z`` passes.

It performs NO public/remote action (no push, tag, upload, registry write).
Exit 0 = preflight passed; exit 1 = blocked (with reasons on stderr).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Final SemVer only — rc/dev are rejected in tag mode until bump_version.py
# learns to sync every surface for pre-release identifiers.
SEMVER_FINAL = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SEMVERISH = r"[0-9][^\s\"]*"


class Result:
    def __init__(self, ok: bool, errors: list[str]) -> None:
        self.ok = ok
        self.errors = errors


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )


# ---------------------------------------------------------------------------
# Version surfaces (mirror of bump_version.py's write set, read-only)
# ---------------------------------------------------------------------------


def _pyproject_version(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    m = re.search(r'(?m)^\s*version\s*=\s*"(' + _SEMVERISH + r')"', path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _json_get(path: Path, *keys):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for k in keys:
        if isinstance(data, dict):
            data = data.get(k)
        elif isinstance(data, list) and isinstance(k, int) and 0 <= k < len(data):
            data = data[k]
        else:
            return None
    return data


def collect_versions(root: Path) -> dict[str, str | None]:
    """Read the package version from every version-bearing surface."""
    out: dict[str, str | None] = {}
    out["pyproject.toml"] = _pyproject_version(root)

    init = root / "src" / "piia_engram" / "__init__.py"
    if init.is_file():
        m = re.search(r'(?m)^__version__\s*=\s*"(' + _SEMVERISH + r')"', init.read_text(encoding="utf-8"))
        out["src/piia_engram/__init__.py"] = m.group(1) if m else None
    else:
        out["src/piia_engram/__init__.py"] = None

    server = root / ".mcp" / "server.json"
    out[".mcp/server.json:version"] = _json_get(server, "version")
    out[".mcp/server.json:packages[0].version"] = _json_get(server, "packages", 0, "version")
    from_value = None
    runtime_args = _json_get(server, "packages", 0, "runtimeArguments")
    if isinstance(runtime_args, list):
        for arg in runtime_args:
            if isinstance(arg, dict) and arg.get("name") == "--from":
                from_value = arg.get("value")
                break
    if isinstance(from_value, str):
        m = re.search(r"piia-engram==(" + _SEMVERISH + r")", from_value)
        out[".mcp/server.json:--from"] = m.group(1) if m else None
    else:
        out[".mcp/server.json:--from"] = None

    out[".claude-plugin/plugin.json"] = _json_get(root / ".claude-plugin" / "plugin.json", "version")

    glama = root / "glama.yaml"
    if glama.is_file():
        m = re.search(r"(?m)^\s*version:\s*(" + _SEMVERISH + r")", glama.read_text(encoding="utf-8"))
        out["glama.yaml"] = m.group(1) if m else None
    else:
        out["glama.yaml"] = None

    out["docs/public-facts.json"] = _json_get(root / "docs" / "public-facts.json", "local_dev_version")

    readme = root / "README.md"
    if readme.is_file():
        m = re.search(r"Version frame \| \*\*v(" + _SEMVERISH + r")\*\*", readme.read_text(encoding="utf-8"))
        out["README.md"] = m.group(1) if m else None
    else:
        out["README.md"] = None

    readme_zh = root / "README.zh-CN.md"
    if readme_zh.is_file():
        m = re.search(r"版本口径 \| \*\*v(" + _SEMVERISH + r")\*\*", readme_zh.read_text(encoding="utf-8"))
        out["README.zh-CN.md"] = m.group(1) if m else None
    else:
        out["README.zh-CN.md"] = None

    return out


def check_version_consistency(root: Path) -> list[str]:
    """Return a list of version-drift errors ([] means all surfaces agree)."""
    versions = collect_versions(root)
    base = versions.get("pyproject.toml")
    errors: list[str] = []
    if not base:
        return ["pyproject.toml: could not read [project].version"]
    for label, value in versions.items():
        if value is None:
            errors.append(f"{label}: version not found")
        elif value != base:
            errors.append(f"{label}: version {value!r} != pyproject {base!r}")
    return errors


# ---------------------------------------------------------------------------
# Git-tree-bound evidence checks
# ---------------------------------------------------------------------------


def evidence_in_head(root: Path, version: str) -> bool:
    """True iff release-evidence/v<version>.md exists in the HEAD commit tree.

    This is stricter than ``Path.exists()`` / ``git ls-files`` on purpose: only
    what is in HEAD will end up in the tag, so that is what must be verified.
    """
    rel = f"release-evidence/v{version}.md"
    return _git(root, "cat-file", "-e", f"HEAD:{rel}").returncode == 0


def _evidence_tracked(root: Path, version: str) -> bool:
    rel = f"release-evidence/v{version}.md"
    return _git(root, "ls-files", "--error-unmatch", rel).returncode == 0


def _evidence_allowlisted(root: Path, version: str) -> bool:
    allow = root / ".publishallow"
    if not allow.is_file():
        return False
    rel = f"release-evidence/v{version}.md"
    lines = [line.strip() for line in allow.read_text(encoding="utf-8").splitlines()]
    return rel in lines


def _local_tag_exists(root: Path, tag: str) -> bool:
    return bool(_git(root, "tag", "--list", tag).stdout.strip())


def _worktree_clean(root: Path) -> bool:
    return _git(root, "status", "--porcelain").stdout.strip() == ""


def run_release_gate(root: Path, version: str) -> tuple[int, str]:
    script = root / "scripts" / "check_release_gate.py"
    if not script.is_file():
        return 1, f"missing {script}"
    proc = subprocess.run(
        [sys.executable, str(script), "--version", version],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def preflight(root: str | Path, *, tag: str | None = None, require_clean: bool = True) -> Result:
    root = Path(root)
    errors: list[str] = list(check_version_consistency(root))
    version = _pyproject_version(root)

    if tag is not None:
        if not version or not SEMVER_FINAL.match(version):
            errors.append(
                f"pyproject version {version!r} is not a final SemVer X.Y.Z "
                "(pre-release identifiers are not releasable yet)"
            )
        expected = f"v{version}" if version else None
        if expected is None or tag != expected:
            errors.append(f"--tag {tag!r} != expected {expected!r} (\"v\" + pyproject version)")

        if version:
            if require_clean and not _worktree_clean(root):
                errors.append("working tree is not clean — commit or stash so HEAD is exactly what gets tagged")
            if _local_tag_exists(root, tag):
                errors.append(f"local tag {tag} already exists")
            if not evidence_in_head(root, version):
                errors.append(
                    f"release-evidence/v{version}.md is not in the HEAD tree — "
                    "commit it BEFORE tagging (a tag freezes the tree)"
                )
            else:
                if not _evidence_tracked(root, version):
                    errors.append(f"release-evidence/v{version}.md is not git-tracked")
                if not _evidence_allowlisted(root, version):
                    errors.append(f"release-evidence/v{version}.md is not listed in .publishallow")
                rc, out = run_release_gate(root, version)
                if rc != 0:
                    last = out.splitlines()[-1] if out else "see check_release_gate.py"
                    errors.append(f"release gate failed for v{version}: {last}")

    return Result(ok=not errors, errors=errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Structural release preflight (no remote actions).")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--tag", default=None, help="intended tag, e.g. v4.12.0 — enables the full pre-tag gate")
    parser.add_argument("--allow-dirty", action="store_true", help="skip the clean-worktree check (NOT for real releases)")
    args = parser.parse_args(argv)

    result = preflight(Path(args.root), tag=args.tag, require_clean=not args.allow_dirty)
    if result.ok:
        scope = f"for {args.tag}" if args.tag else "(version consistency)"
        print(f"[OK] release preflight passed {scope}")
        return 0
    print("[FAIL] release preflight blocked:", file=sys.stderr)
    for err in result.errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
