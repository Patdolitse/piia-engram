"""Install an opt-in git pre-commit hook that runs the sanitization scanner.

v3.31 P1-1: catches secrets / internal-disclosure patterns *before* the
commit lands, instead of waiting for CI (guard-strategic-files.yml +
publish.yml). The hook scans only staged files, so it's fast.

Usage (from repo root):

    python scripts/install_git_hooks.py          # install
    python scripts/install_git_hooks.py --uninstall

The hook runs two checks:

    python scripts/release_sanitize_check.py --staged --internal --strict
    python scripts/check_publish_allowlist.py

The first blocks the commit (exit 1) on any HIGH hit; warn-level hits
block too while ``BLOCK_ON_WARN`` is ``--strict`` (flip it below if that's
too aggressive). The second enforces the default-deny publish allowlist:
because ``git ls-files`` already includes files you just ``git add``-ed,
staging a new tracked file that isn't on ``.publishallow`` blocks the
commit — the same gate CI runs, moved one step earlier.

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

HOOK_MARKER = "# piia-engram-sanitize-hook v4"

# Older markers we still recognize as "ours" so install --upgrade can
# safely overwrite a hook a previous version installed.
_KNOWN_MARKERS = (
    "# piia-engram-sanitize-hook v1",
    "# piia-engram-sanitize-hook v2",
    "# piia-engram-sanitize-hook v3",
    "# piia-engram-sanitize-hook v4",
)

# Set to "" to make warn-level hits non-blocking (HIGH always blocks).
BLOCK_ON_WARN = "--strict"

HOOK_BODY = f"""#!/bin/sh
{HOOK_MARKER}
# Auto-installed by scripts/install_git_hooks.py — runs the release
# sanitization scanner AND the publish-allowlist check before each commit.
#
# To bypass once (NOT recommended): git commit --no-verify

# Find a WORKING python. The Windows Store alias stub (…WindowsApps\\python) is
# on PATH for many users but exits non-zero without running any code, which
# would falsely block EVERY commit — so each candidate is validated by actually
# executing a no-op. ENGRAM_PYTHON overrides when it is set and works.
_works() {{ "$1" -c "import sys" >/dev/null 2>&1; }}
PY="${{ENGRAM_PYTHON:-}}"
if [ -z "$PY" ] || ! _works "$PY"; then
    PY=""
    for _cand in python3 python py; do
        if command -v "$_cand" >/dev/null 2>&1 && _works "$_cand"; then
            PY="$_cand"; break
        fi
    done
fi
if [ -z "$PY" ]; then
    echo "[pre-commit] no working python found; skipping pre-commit checks" >&2
    exit 0
fi

rc=0

"$PY" scripts/release_sanitize_check.py --staged --internal {BLOCK_ON_WARN}
if [ "$?" -ne 0 ]; then rc=1; fi

# Default-deny publish allowlist: git ls-files already sees staged adds,
# so a newly tracked file missing from .publishallow blocks the commit.
# --staged reads .publishallow from the index (what's actually committed),
# not an unstaged working-tree edit.
"$PY" scripts/check_publish_allowlist.py --staged
if [ "$?" -ne 0 ]; then rc=1; fi

if [ "$rc" -ne 0 ]; then
    echo "" >&2
    echo "[pre-commit] a pre-commit check blocked the commit." >&2
    echo "  Fix the flagged content, or bypass with: git commit --no-verify" >&2
    exit 1
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
        if not any(marker in existing for marker in _KNOWN_MARKERS):
            print(f"[warn] {hook_path} already exists and is NOT ours.")
            print("       Refusing to overwrite. Inspect it, then either")
            print("       merge our scan call in or remove the file and re-run.")
            return 1
        # ours (any known version) — safe to overwrite (upgrade)

    hook_path.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    # chmod +x (no-op effect on Windows but harmless)
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[ok] installed pre-commit hook → {hook_path}")
    print("     Runs: release_sanitize_check.py --staged --internal "
          f"{BLOCK_ON_WARN}")
    print("           check_publish_allowlist.py")
    print("     Bypass once with: git commit --no-verify")
    return 0


def uninstall() -> int:
    hook_path = _git_dir() / "hooks" / "pre-commit"
    if not hook_path.exists():
        print("[ok] no pre-commit hook to remove.")
        return 0
    existing = hook_path.read_text(encoding="utf-8", errors="ignore")
    if not any(marker in existing for marker in _KNOWN_MARKERS):
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
