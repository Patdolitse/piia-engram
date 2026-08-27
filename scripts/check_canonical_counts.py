#!/usr/bin/env python3
"""Verify a REAL full-suite JUnit run matches docs/public-facts.json exactly.

The manifest's test_collected was previously checked from a collect-only run,
and passed/skipped were only internally consistent arithmetic. This gate
parses the JUnit XML of an actual full pytest execution and requires every
count to match the manifest, ending the hand-edit drift of release days.
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("junit", help="path to pytest --junitxml output")
    ap.add_argument("--manifest", default="docs/public-facts.json")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    facts = manifest.get("facts", {})
    want = {
        "test_collected": int(facts["test_collected"]),
        "test_passed": int(facts["test_passed"]),
        "test_skipped": int(facts["test_skipped"]),
    }
    got_raw = junit_counts(Path(args.junit))
    got = {
        "test_collected": got_raw["collected"],
        "test_passed": got_raw["passed"],
        "test_skipped": got_raw["skipped"],
    }

    if got_raw["failed"] or got_raw["errors"]:
        print(
            f"::error::canonical count gate: suite itself is not green "
            f"(failures={got_raw['failed']}, errors={got_raw['errors']})"
        )
        return 1

    problems = [f"{k}: manifest={v} junit={got[k]}" for k, v in want.items() if got[k] != v]
    if problems:
        print("::error::canonical count drift vs docs/public-facts.json:")
        for p in problems:
            print(f"  - {p}")
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
