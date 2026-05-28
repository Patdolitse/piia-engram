"""Publish allowlist checker (WHITELIST enforcement).

Fails if any git-tracked file is NOT covered by an entry in
``.publishallow``. This is the inverse of .gitignore/.guardignore: those
say what to keep OUT; this says what is the ONLY thing allowed IN.

Default-deny posture: a file that nobody explicitly allowed is treated
as private and blocks the build, forcing a conscious decision.

Run from repo root:

    python scripts/check_publish_allowlist.py            # report + exit 1 on violations
    python scripts/check_publish_allowlist.py --list     # just print uncovered files

Exit codes:
- 0  every tracked file is covered by .publishallow
- 1  one or more tracked files are not covered (publish-policy violation)
- 2  setup error (no .publishallow, not a git repo)
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

ALLOWLIST_FILE = ".publishallow"
_GLOB_CHARS = set("*?[")


def _load_allowlist() -> list[str]:
    path = Path(ALLOWLIST_FILE)
    if not path.is_file():
        print(f"[error] {ALLOWLIST_FILE} not found in repo root", file=sys.stderr)
        sys.exit(2)
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.replace("\\", "/"))
    return patterns


def _git_tracked_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], text=True, encoding="utf-8"
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[error] git ls-files failed: {exc}", file=sys.stderr)
        sys.exit(2)
    return [line.replace("\\", "/") for line in out.splitlines() if line]


def _matches(rel_path: str, pattern: str) -> bool:
    """Does *rel_path* satisfy a single allowlist *pattern*?

    - ``foo/**``        → any path under foo/
    - ``foo/bar.py``    → exact match
    - ``*.md`` / globs  → fnmatch on the full path
    """
    if pattern.endswith("/**"):
        prefix = pattern[:-2]  # keep trailing slash, drop the **
        return rel_path.startswith(prefix)
    if any(c in pattern for c in _GLOB_CHARS):
        # fnmatch over full path; also allow matching a basename glob
        return fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
            rel_path, f"*/{pattern}"
        )
    return rel_path == pattern


def find_uncovered(patterns: list[str], tracked: list[str]) -> list[str]:
    uncovered: list[str] = []
    for rel in tracked:
        if not any(_matches(rel, pat) for pat in patterns):
            uncovered.append(rel)
    return uncovered


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    ap.add_argument("--list", action="store_true",
                    help="Print uncovered files only (still exits 1 if any).")
    args = ap.parse_args()

    patterns = _load_allowlist()
    tracked = _git_tracked_files()
    uncovered = find_uncovered(patterns, tracked)

    if not uncovered:
        print(f"[OK] all {len(tracked)} tracked files are covered by "
              f"{ALLOWLIST_FILE} ({len(patterns)} patterns).")
        return 0

    if args.list:
        for f in uncovered:
            print(f)
        return 1

    print(f"::error::{len(uncovered)} tracked file(s) are NOT on the publish "
          f"allowlist ({ALLOWLIST_FILE}):")
    for f in uncovered:
        print(f"  - {f}")
    print("")
    print("Default-deny publish policy: anything not on the allowlist is")
    print("treated as private. To resolve each file, either:")
    print(f"  1. It SHOULD be public  → add a matching entry to {ALLOWLIST_FILE}")
    print("     (for a new docs/*.md, this addition is your review checkpoint —")
    print("      confirm the doc carries no internal-only / reverse-disclosure content)")
    print("  2. It should NOT be public → git rm --cached it + add to .gitignore")
    return 1


if __name__ == "__main__":
    sys.exit(main())
