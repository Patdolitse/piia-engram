"""Render a public-safe Cursor forum reply draft from aggregate evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path_text: str) -> dict[str, Any]:
    evidence = Path(path_text)
    if not evidence.exists():
        raise FileNotFoundError("evidence file not found")
    try:
        data = json.loads(evidence.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("evidence file must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("evidence must be public-safe aggregate JSON")
    if data.get("schema") != "anchor_live_smoke_evidence.v1" or data.get("public_safe") is not True:
        raise ValueError("evidence must be public-safe aggregate JSON")
    return data


def _count(payload: dict[str, Any], section: str, key: str) -> int:
    block = payload.get(section)
    if not isinstance(block, dict):
        return 0
    try:
        return max(0, int(block.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def render_reply(payload: dict[str, Any]) -> str:
    checked = _count(payload, "anchors", "checked")
    valid = _count(payload, "anchors", "valid")
    invalid = _count(payload, "anchors", "invalid")
    unknown = _count(payload, "anchors", "unknown")
    superseded = _count(payload, "anchors", "superseded")
    demoted = _count(payload, "anchors", "demoted_to_staging")
    runs = _count(payload, "live_smoke", "runs")
    passed = _count(payload, "live_smoke", "passed")
    failed = _count(payload, "live_smoke", "failed")

    return "\n".join([
        "Owner confirmation required before posting.",
        "",
        "Draft reply:",
        "",
        (
            "Thanks again for the thoughtful thread. We ran a controlled harness plus a small historical replay "
            "and kept the evidence aggregate-only."
        ),
        "",
        (
            f"- Anchor checks: {checked} anchor checks; {valid} valid, "
            f"{invalid} invalid, {unknown} unknown, {superseded} superseded, "
            f"{demoted} demoted back to review/staging."
        ),
        f"- LIVE_SMOKE: {runs} LIVE_SMOKE runs; {passed} passed, {failed} failed.",
        (
            "- What changed in practice: an AI guess does not silently become a fact; "
            "anchor-backed facts keep an explicit evidence basis, and broken evidence "
            "moves them back toward review instead of quiet trust."
        ),
        (
            "- Design rule: the system carries the confirmation source; it does not rely on the next model "
            "remembering that something was only a guess."
        ),
        (
            "- Trust rule: agent-written trust fields are not trusted just because they appear in normal writes; "
            "the governed path has to confirm the supporting evidence."
        ),
        (
            "- Failure rule: unknown is not the same as false, and an anchor becoming reachable again does not "
            "automatically make the claim trusted."
        ),
        (
            "- Scope rule: an anchor checks structural evidence, not semantic truth; it can say whether the "
            "supporting record is still parseable/reachable, not whether the real-world claim is true forever."
        ),
        (
            "- Caveats: this is a local dataset from a single local run window, "
            "not a broad benchmark and not a statistically significant result. "
            "We are not sharing raw data, memory bodies, local paths, or private transcripts."
        ),
        "",
        "Happy to share the sanitized reproduction harness or an aggregate metrics table if useful.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, help="Aggregate evidence JSON file.")
    args = parser.parse_args()

    try:
        payload = _load(args.evidence)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_reply(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
