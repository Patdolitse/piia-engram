"""Offline client-compatibility A/B evidence harness (synthetic, no network).

This harness proves, *without any live provider auth or network*, the single
defensible client-compatibility claim Engram can stand behind today: with the
Engram MCP read surface enabled a client sees strictly more knowledge *signals*
than with it disabled, and neither arm pollutes the copied store.

It does this entirely on copied/synthetic data:

1. Build a fixed synthetic Engram knowledge set (fake lessons/decisions).
2. Copy it into two isolated arm directories under a temp base:
   - ``engram-on``  : a read-only recall projection runs over the copied store.
   - ``engram-off`` : no Engram surface, so zero signals are available.
3. Snapshot each arm's directory tree before and after, proving the read-only
   projection mutated nothing (directory-level zero pollution).
4. Emit a deterministic, metadata-only A/B report plus a public-safe summary
   routed through the client-validation claim guard.

Safety invariants (enforced, not just documented):
- Writes only under the caller-provided temp base; never touches ``~/.engram``.
- The recall projection is read-only and content is projected to summary/meta,
  so no raw body or absolute path appears in the report.
- Output is byte-stable across temp dirs and runs (no timestamps, no freshness),
  so the JSON can be diffed as evidence.

Run from the repo root::

    python demos/client_ab_evidence_harness.py            # human summary
    python demos/client_ab_evidence_harness.py --json      # machine-readable

The synthetic corpus lives in this file so the harness has no external inputs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram import client_validation as cv  # noqa: E402
from piia_engram import recall as _recall  # noqa: E402

# Fixed synthetic knowledge. Fake content only; never a real memory.
_SYNTHETIC_LESSONS: list[dict[str, Any]] = [
    {"id": "L1", "summary": "synthetic lesson alpha", "tier": "verified", "domain": "demo"},
    {"id": "L2", "summary": "synthetic lesson beta", "tier": "verified", "domain": "demo"},
]
_SYNTHETIC_DECISIONS: list[dict[str, Any]] = [
    {
        "id": "D1",
        "question": "synthetic question one?",
        "choice": "synthetic choice one",
        "tier": "verified",
        "domain": "demo",
    },
]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_synthetic_store(root: Path) -> Path:
    """Create a clean synthetic Engram knowledge dir under *root*."""
    knowledge = root / "knowledge"
    _write_json(knowledge / "lessons.json", _SYNTHETIC_LESSONS)
    _write_json(knowledge / "decisions.json", _SYNTHETIC_DECISIONS)
    return root


def _read_only_recall_signal_count(store_root: Path) -> int:
    """Count knowledge signals a read-only recall projection surfaces.

    Reads the copied store's knowledge files, runs the *pure* recall projection
    (``build_recall_payload``) with freshness off so output is deterministic, and
    returns the surfaced item count. Never writes; never echoes a body.
    """
    knowledge = store_root / "knowledge"
    lessons = json.loads((knowledge / "lessons.json").read_text(encoding="utf-8"))
    decisions = json.loads((knowledge / "decisions.json").read_text(encoding="utf-8"))
    payload = _recall.build_recall_payload(
        relevant_knowledge=list(lessons) + list(decisions),
        include_freshness=False,
        token_budget=4000,
    )
    return len(payload.get("knowledge", []))


def _run_arm(base: Path, arm: str, *, engram_enabled: bool) -> dict[str, Any]:
    """Copy the synthetic store into an arm dir and run (or skip) the projection."""
    source = base / "synthetic-source"
    arm_root = base / arm
    if arm_root.exists():
        shutil.rmtree(arm_root)
    shutil.copytree(source, arm_root)

    before = cv.snapshot_tree(arm_root)
    if engram_enabled:
        surfaced = _read_only_recall_signal_count(arm_root)
    else:
        surfaced = 0  # no Engram MCP surface → no signals available
    after = cv.snapshot_tree(arm_root)

    return cv.build_ab_arm(
        arm=arm,
        engram_enabled=engram_enabled,
        surfaced_signal_count=surfaced,
        before_tree=before,
        after_tree=after,
    )


def run_harness(base: Path, *, client_id: str = "synthetic-offline") -> dict[str, Any]:
    """Run the full offline A/B and return a deterministic metadata-only report."""
    base.mkdir(parents=True, exist_ok=True)
    source = base / "synthetic-source"
    if source.exists():
        shutil.rmtree(source)
    build_synthetic_store(source)

    # The "live store" stand-in: a separate fingerprint that must be untouched.
    live_before = cv.tree_digest(cv.snapshot_tree(source))

    on_arm = _run_arm(base, "engram-on", engram_enabled=True)
    off_arm = _run_arm(base, "engram-off", engram_enabled=False)

    live_after = cv.tree_digest(cv.snapshot_tree(source))

    evidence = cv.build_ab_evidence(
        on_arm=on_arm,
        off_arm=off_arm,
        client_id=client_id,
        live_store_digest_before=live_before,
        live_store_digest_after=live_after,
    )
    evidence["public_summary"] = cv.build_public_safe_summary(evidence)
    return evidence


def render_text(report: dict[str, Any]) -> str:
    on = report.get("on_arm", {})
    off = report.get("off_arm", {})
    summary = report.get("public_summary", {})
    lines = [
        "Engram offline client A/B evidence harness (synthetic, no network)",
        f"  client: {report.get('client_id', '')}",
        f"  engram-on  signals: {on.get('surfaced_signal_count', 0)}  "
        f"zero_pollution={on.get('zero_pollution_clean')}",
        f"  engram-off signals: {off.get('surfaced_signal_count', 0)}  "
        f"zero_pollution={off.get('zero_pollution_clean')}",
        f"  signal differential: {report.get('signal_differential', 0)} "
        f"(positive={report.get('differential_positive')})",
        f"  live store untouched: {report.get('live_store_untouched')}",
        f"  claim allowed: {summary.get('claim_allowed')}",
        f"  overall: {'PASS' if report.get('overall_passed') else 'FAIL'}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline synthetic client A/B evidence harness."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp base and print its path.")
    parser.add_argument("--client-id", default="synthetic-offline")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="engram-client-ab-"))
    try:
        report = run_harness(base, client_id=args.client_id)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_text(report))
        if args.keep:
            print(f"Kept base: {base}")
        return 0 if report["overall_passed"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
