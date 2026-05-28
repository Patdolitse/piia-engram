"""Deterministic release gate — refuse to publish without review evidence.

WHY: playbooks/process docs are *pull-based* — they only help if a human or
AI remembers to consult them. For a MANDATORY gate (no release without
review) that is not enough: the moment you most need the gate (heads-down
shipping) is exactly when it gets skipped. So this script makes the
environment enforce it: the publish workflow runs it, and publishing FAILS
unless a matching, complete evidence file exists. It does not trust the
agent to remember.

Evidence lives at ``release-evidence/v<version>.md`` (tracked, so CI on a
fresh checkout sees it) and must record that each required gate passed:

    # Release evidence — v3.34.0

    - self-review: passed
    - codex-review: passed        # independent external (Codex) review
    - tests: pass                 # full pytest suite green
    - eval-gate: pass             # or: n/a (no retrieval/quality change)

Required markers: ``self-review``, ``codex-review``, ``tests``. Each must be
on its own line as ``<marker>: <value>`` with a passing value
(passed/pass/ok/green/yes). ``eval-gate`` is required to be present but may
be ``n/a``.

Run from repo root:

    python scripts/check_release_gate.py            # check current version
    python scripts/check_release_gate.py --version 3.34.0

Exit codes:
- 0  evidence present and complete
- 1  evidence missing or incomplete (blocks release)
- 2  setup error (no pyproject, etc.)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_MARKERS = ("self-review", "codex-review", "tests")
PRESENCE_ONLY = ("eval-gate",)  # must appear; "n/a" is acceptable
_PASS_VALUES = {"passed", "pass", "ok", "green", "yes", "done"}
_NA_VALUES = {"n/a", "na", "none", "skip", "skipped"}

EVIDENCE_DIR = "release-evidence"


def _pyproject_version(root: Path) -> str:
    path = root / "pyproject.toml"
    if not path.is_file():
        print(f"[error] {path} not found", file=sys.stderr)
        sys.exit(2)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*version\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    print("[error] version not found in pyproject.toml", file=sys.stderr)
    sys.exit(2)


def _parse_markers(text: str) -> dict[str, str]:
    """Collect ``marker: value`` lines (marker lowercased)."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*[-*]?\s*([A-Za-z][\w-]*)\s*[:=]\s*(.+?)\s*$", line)
        if m:
            value = m.group(2).strip()
            # Drop an inline "# comment" so e.g. "passed   # note" reads as "passed".
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip().lower()
            found[m.group(1).strip().lower()] = value
    return found


def check_release_gate(version: str, root: Path) -> tuple[bool, list[str]]:
    """Return (ok, problems) for the evidence of ``version``."""
    problems: list[str] = []
    evidence = root / EVIDENCE_DIR / f"v{version}.md"
    if not evidence.is_file():
        return False, [
            f"missing evidence file: {EVIDENCE_DIR}/v{version}.md "
            f"(record self-review / codex-review / tests / eval-gate there)"
        ]

    markers = _parse_markers(evidence.read_text(encoding="utf-8"))

    for marker in REQUIRED_MARKERS:
        if marker not in markers:
            problems.append(f"missing required marker '{marker}:'")
        elif markers[marker] not in _PASS_VALUES:
            problems.append(
                f"marker '{marker}' is '{markers[marker]}', expected a passing "
                f"value ({'/'.join(sorted(_PASS_VALUES))})"
            )

    for marker in PRESENCE_ONLY:
        if marker not in markers:
            problems.append(f"missing required marker '{marker}:' (use n/a if not applicable)")
        elif markers[marker] not in (_PASS_VALUES | _NA_VALUES):
            problems.append(
                f"marker '{marker}' is '{markers[marker]}', expected pass or n/a"
            )

    return (not problems), problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--version", help="Version to check (default: from pyproject.toml)")
    ap.add_argument("--root", default=".", help="Repo root (default: cwd)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    version = args.version or _pyproject_version(root)

    ok, problems = check_release_gate(version, root)
    if ok:
        print(f"[OK] release gate satisfied for v{version} "
              f"({EVIDENCE_DIR}/v{version}.md complete).")
        return 0

    print(f"::error::release gate BLOCKED for v{version}:")
    for p in problems:
        print(f"  - {p}")
    print("")
    print("Publishing is blocked until the evidence file records that the")
    print("mandatory gates passed (self-review + codex-review + tests, and an")
    print("eval-gate line). This gate is enforced by CI so it cannot be skipped.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
