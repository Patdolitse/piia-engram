#!/usr/bin/env python3
"""Verify a REAL full-suite JUnit run matches docs/public-facts.json exactly.

The manifest's test_collected was previously checked from a collect-only run,
and passed/skipped were only internally consistent arithmetic. This gate
parses the JUnit XML of an actual full pytest execution and requires every
count to match the manifest, ending the hand-edit drift of release days.

``--patch-output PATH`` (v4.19): when the suite is fully green but the counts
drift, ALSO emit a unified diff of the manifest's three count fields as a
review+apply artifact. The gate still exits 1 on drift — the patch never
auto-commits, it only removes the hand-editing step for the human.
A red suite, an empty JUnit, or a malformed XML produce NO patch and fail
closed (a bad run must never turn into a confidently wrong suggestion).
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    # pytest JUnit: <testsuites><testsuite tests=... failures=... /></testsuites>
    suites = root.findall(".//testsuite")
    collected = passed = skipped = failed = errors = 0
    for suite in suites:
        collected += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    passed = collected - failed - errors - skipped
    return {
        "collected": collected,
        "passed": passed,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def _patched_manifest_text(manifest_path: Path, got: dict[str, int]) -> str | None:
    """Render the manifest with ONLY the three count facts updated.

    Returns None when the manifest cannot be read/parsed (no patch on a broken
    manifest — the gate's own error already covers it).
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    facts = manifest.get("facts")
    if not isinstance(facts, dict):
        return None
    facts["test_collected"] = int(got["test_collected"])
    facts["test_passed"] = int(got["test_passed"])
    facts["test_skipped"] = int(got["test_skipped"])
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def write_patch(manifest_path: Path, patch_path: Path, got: dict[str, int]) -> bool:
    """Write a unified diff of the manifest counts; False when impossible."""
    patched = _patched_manifest_text(manifest_path, got)
    if patched is None:
        return False
    current = manifest_path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile="docs/public-facts.json (manifest)",
        tofile=f"docs/public-facts.json (counts from JUnit: {got['test_passed']} passed / "
        f"{got['test_skipped']} skipped / {got['test_collected']} collected)",
    )
    patch_path.write_text("".join(diff), encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("junit", help="path to pytest --junitxml output")
    ap.add_argument("--manifest", default="docs/public-facts.json")
    ap.add_argument(
        "--patch-output",
        default=None,
        help="on a green-suite count drift, write a unified diff of the manifest "
        "counts here for review+apply (the gate still fails; never auto-merges)",
    )
    args = ap.parse_args()

    junit_path = Path(args.junit)
    try:
        got_raw = junit_counts(junit_path)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(
            f"::error::canonical count gate: JUnit output is unreadable or malformed "
            f"({exc}); fail closed (no patch from a broken run)"
        )
        return 1
    if got_raw["collected"] <= 0:
        print(
            "::error::canonical count gate: JUnit output contains no tests "
            "(empty suite); fail closed (no patch from an empty run)"
        )
        return 1

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::error::canonical count gate: manifest unreadable ({exc})")
        return 1
    facts = manifest.get("facts", {})
    want = {
        "test_collected": int(facts["test_collected"]),
        "test_passed": int(facts["test_passed"]),
        "test_skipped": int(facts["test_skipped"]),
    }
    got = {
        "test_collected": got_raw["collected"],
        "test_passed": got_raw["passed"],
        "test_skipped": got_raw["skipped"],
    }

    if got_raw["failed"] or got_raw["errors"]:
        print(
            f"::error::canonical count gate: suite itself is not green "
            f"(failures={got_raw['failed']}, errors={got_raw['errors']}); no patch"
        )
        return 1

    problems = [f"{k}: manifest={v} junit={got[k]}" for k, v in want.items() if got[k] != v]
    if problems:
        print("::error::canonical count drift vs docs/public-facts.json:")
        for p in problems:
            print(f"  - {p}")
        if args.patch_output:
            if write_patch(Path(args.manifest), Path(args.patch_output), got):
                print(
                    f"::notice::public-facts patch written to {args.patch_output} "
                    "(review + apply; the gate stays red until the manifest is refreshed)"
                )
            else:
                print("::error::could not render the public-facts patch (manifest shape)")
        else:
            print(
                "Refresh the manifest from THIS run's JUnit numbers (they are the "
                "canonical environment: ubuntu-latest, Python 3.12, plugin autoload off)."
            )
        return 1

    print(
        f"[ok] canonical counts match: {want['test_passed']} passed / "
        f"{want['test_skipped']} skipped / {want['test_collected']} collected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
