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
    - codex-review: passed        # independent Codex review
    - claude-review: passed       # independent Claude acceptance review
    - tests: pass                 # full pytest suite green
    - eval-gate: pass             # or: n/a (no retrieval/quality change)
    - negative-control: passed    # R1; or n/a (no security-sensitive change)
    - field-assertion-audit: passed  # R5; or n/a (no security-sensitive module touched)

Required markers: ``self-review``, ``codex-review``, ``claude-review``,
``tests``. Each must be on its own line as ``<marker>: <value>`` with a passing value
(passed/pass/ok/green/yes). ``eval-gate``, ``negative-control`` and
``field-assertion-audit`` are required to be present but may be ``n/a``.

The last two encode the self-test admission ruleset (R1/R5) derived from the
a5 corpus-encryption Codex audits, where "the tests I wrote all pass" hid four
plaintext-leak bugs:

- ``negative-control`` (R1): for any security-sensitive change, the new
  regression tests must have been shown to FAIL on the pre-fix code (a green
  test that also passes on the buggy code proves nothing). Record ``passed``
  once you have run the new tests against the old commit and seen them red;
  use ``n/a`` only when the release touches no security-sensitive behaviour.
- ``field-assertion-audit`` (R5): for any change to a security-sensitive
  module (encryption, redaction, permission gating), every free-text field
  that could carry secret content must have an on-disk assertion proving it
  is not written in the clear — "it looks safe when I read the code" is not
  evidence. Record ``passed`` once the field-vs-assertion checklist is
  complete; use ``n/a`` when no such module was touched.

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

REQUIRED_MARKERS = ("self-review", "codex-review", "claude-review", "tests")
# Must appear; "n/a" is acceptable. eval-gate guards retrieval/quality
# regressions; negative-control (R1) and field-assertion-audit (R5) encode the
# self-test admission ruleset — see module docstring.
PRESENCE_ONLY = ("eval-gate", "negative-control", "field-assertion-audit")
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
    # Optional list prefix: -, *, + (with optional checkbox), or 1. / 1)
    prefix = r"(?:[-*+]\s*(?:\[[ xX]\]\s*)?|\d+[.)]\s*)?"
    for line in text.splitlines():
        m = re.match(rf"\s*{prefix}([A-Za-z][\w-]*)\s*[:=]\s*(.+?)\s*$", line)
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
            f"(record self-review / codex-review / claude-review / tests / eval-gate there)"
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
    print("mandatory gates passed (self-review + codex-review + claude-review + tests) and the")
    print("presence-only gates are declared (eval-gate, negative-control,")
    print("field-assertion-audit - each 'passed' or 'n/a'). This gate is enforced")
    print("by CI so it cannot be skipped.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
