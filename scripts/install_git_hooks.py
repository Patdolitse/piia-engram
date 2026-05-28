"""Install an opt-in git pre-commit hook that runs the sanitization scanner.

v3.31 P1-1: catches secrets / internal-disclosure patterns *before* the
commit lands, instead of waiting for CI (guard-strategic-files.yml +
publish.yml). The hook scans only staged files, so it's fast.

Usage (from repo root):

    python scripts/install_git_hooks.py          # install
    python scripts/install_git_hooks.py --uninstall

The hook runs:

    python scripts/release_sanitize_check.py --staged --internal --strict

and blocks the commit (exit 1) on any HIGH hit. warn-level hits print a
notice but don't block unless you keep --strict (default here blocks on
warn too — flip ``BLOCK_ON_WARN`` below if that's too aggressive).

Cross-platform: the hook is a portable POSIX shell script. On Windows,
git invokes it through the bundled Git-Bash, so it works there too.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

HOOK_MARKER = "# piia-engram-sanitize-hook v1"

# Set to "" to make warn-level hits non-blocking (HIGH always blocks).
BLOCK_ON_WARN = "--strict"

HOOK_BODY = f"""#!/bin/sh
{HOOK_MARKER}
# Auto-installed by scripts/install_git_hooks.py — runs the release
# sanitization scanner on staged files before each commit.
#
# To bypass once (NOT recommended): git commit --no-verify

# Find a python interpreter. Prefer one already on PATH; fall back to
# the ENGRAM_PYTHON env var if set.
PY="${{ENGRAM_PYTHON:-}}"
if [ -z "$PY" ]; then
    if command -v python >/dev/null 2>&1; then PY=python;
    elif command -v python3 >/dev/null 2>&1; then PY=python3;
    else
        echo "[pre-commit] no python found; skipping sanitize scan" >&2
        exit 0
    fi
fi

"$PY" scripts/release_sanitize_check.py --staged --internal {BLOCK_ON_WARN}
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "" >&2
    echo "[pre-commit] sanitize scan blocked the commit (exit $rc)." >&2
    echo "  Fix the flagged content, or bypass with: git commit --no-verify" >&2
    exit "$rc"
fi
exit 0
"""


def _git_dir() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"], text=True, encoding="utf-8"
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[error] not a git repo: {exc}", file=sys.stderr)
        sys.exit(2)
    return Path(out)


def install() -> int:
    hook_path = _git_dir() / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="ignore")
        if HOOK_MARKER not in existing:
            print(f"[warn] {hook_path} already exists and is NOT ours.")
            print("       Refusing to overwrite. Inspect it, then either")
            print("       merge our scan call in or remove the file and re-run.")
            return 1
        # ours — safe to overwrite (upgrade)

    hook_path.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    # chmod +x (no-op effect on Windows but harmless)
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[ok] installed pre-commit hook → {hook_path}")
    print("     Runs: release_sanitize_check.py --staged --internal "
          f"{BLOCK_ON_WARN}")
    print("     Bypass once with: git commit --no-verify")
    return 0


def uninstall() -> int:
    hook_path = _git_dir() / "hooks" / "pre-commit"
    if not hook_path.exists():
        print("[ok] no pre-commit hook to remove.")
        return 0
    existing = hook_path.read_text(encoding="utf-8", errors="ignore")
    if HOOK_MARKER not in existing:
        print(f"[warn] {hook_path} is not ours; leaving it alone.")
        return 1
    hook_path.unlink()
    print(f"[ok] removed pre-commit hook → {hook_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--uninstall", action="store_true",
                    help="Remove the hook instead of installing it.")
    args = ap.parse_args()
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    sys.exit(main())
