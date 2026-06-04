"""Synthetic memory-store fault-injection harness.

This harness builds a *synthetic* Engram store in an isolated temporary
directory, injects one well-defined corruption at a time, and asserts that the
read-only integrity scan (:mod:`piia_engram.integrity`) detects it — i.e. the
store fails *loud* instead of silently serving corrupt data.

Safety invariants (enforced, not just documented):
- It only ever writes under the caller-provided base directory (a temp dir).
  It never reads, scans, or mutates the user's real ``~/.engram`` store.
- Each fault gets a *fresh* synthetic store, so faults never interact.
- The integrity scan itself is read-only; the harness asserts the on-disk
  bytes are unchanged after scanning each store.

Run from the repo root::

    python demos/store_integrity_harness.py            # human summary
    python demos/store_integrity_harness.py --json      # machine-readable report

The report is metadata-only: it never echoes stored knowledge bodies, only the
fault name, the expected problem code, and whether it was detected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram import integrity  # noqa: E402
from piia_engram.governance import GovernanceLedger, default_ledger_path  # noqa: E402


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_synthetic_store(root: Path) -> Path:
    """Create a clean, healthy synthetic Engram store under *root*.

    Uses only fake data. Returns *root* for chaining.
    """
    knowledge = root / "knowledge"
    _write_json(knowledge / "lessons.json", [
        {"id": "L1", "summary": "synthetic lesson one", "tier": "verified"},
        {"id": "L2", "summary": "synthetic lesson two", "tier": "staging"},
    ])
    _write_json(knowledge / "decisions.json", [
        {"id": "D1", "question": "q?", "choice": "c", "tier": "verified"},
    ])
    _write_json(knowledge / "playbooks.json", [])
    _write_json(knowledge / "relations.json", [
        {"src": "L1", "rel": "led_to", "dst": "D1"},
    ])
    # A valid 2-event governance ledger so the chain verifies clean.
    ledger = GovernanceLedger(default_ledger_path(root))
    ledger.append({"action": "synthetic_seed", "target": "L1"})
    ledger.append({"action": "synthetic_seed", "target": "D1"})
    # A search index newer than the store so a clean store has no stale-index flag.
    index = root / "search_index.db"
    index.write_bytes(b"synthetic-index")
    now = time.time() + 5
    os.utime(index, (now, now))
    return root


# ---------------------------------------------------------------------------
# Fault injectors — each mutates a freshly built synthetic store in ONE way.
# ---------------------------------------------------------------------------


def _inject_truncated_json(root: Path) -> None:
    (root / "knowledge" / "lessons.json").write_text('[{"id": "L1", "summ', encoding="utf-8")


def _inject_invalid_json(root: Path) -> None:
    (root / "knowledge" / "lessons.json").write_text("{this is not json", encoding="utf-8")


def _inject_not_a_list(root: Path) -> None:
    _write_json(root / "knowledge" / "lessons.json", {"id": "L1", "summary": "an object, not a list"})


def _inject_duplicate_ids(root: Path) -> None:
    _write_json(root / "knowledge" / "lessons.json", [
        {"id": "L1", "summary": "a"}, {"id": "L1", "summary": "b — duplicate id"},
    ])


def _inject_stale_index(root: Path) -> None:
    index = root / "search_index.db"
    old = time.time() - 10_000
    os.utime(index, (old, old))
    lessons = root / "knowledge" / "lessons.json"
    now = time.time()
    os.utime(lessons, (now, now))


def _inject_dangling_relation(root: Path) -> None:
    _write_json(root / "knowledge" / "relations.json", [
        {"src": "L1", "rel": "led_to", "dst": "GHOST_ID_NOT_IN_STORE"},
    ])


def _inject_relation_cycle(root: Path) -> None:
    _write_json(root / "knowledge" / "relations.json", [
        {"src": "L1", "rel": "led_to", "dst": "D1"},
        {"src": "D1", "rel": "led_to", "dst": "L1"},
    ])


def _inject_tampered_ledger(root: Path) -> None:
    """Flip a byte in a ledger record body so its hash no longer matches."""
    path = default_ledger_path(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["event"]["target"] = "TAMPERED"  # body changed but hash left stale
    lines[0] = json.dumps(rec, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# fault name -> (injector, expected problem code, "must be unhealthy")
_FAULTS: dict[str, tuple[Callable[[Path], None], str]] = {
    "truncated_json": (_inject_truncated_json, "dataset_corrupt"),
    "invalid_json": (_inject_invalid_json, "dataset_corrupt"),
    "not_a_list": (_inject_not_a_list, "dataset_corrupt"),
    "duplicate_ids": (_inject_duplicate_ids, "duplicate_ids"),
    "stale_index": (_inject_stale_index, "index_stale"),
    "dangling_relation": (_inject_dangling_relation, "dangling_relations"),
    "relation_cycle": (_inject_relation_cycle, "relation_cycle"),
    "tampered_ledger": (_inject_tampered_ledger, "ledger_chain_broken"),
}


def _store_fingerprint(root: Path) -> dict[str, str]:
    """sha256 of every file under *root* — to prove the scan is read-only."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _run_case(base: Path, name: str, injector: Callable[[Path], None], expected_code: str) -> dict[str, Any]:
    root = base / f"store-{name}"
    if root.exists():
        shutil.rmtree(root)
    build_synthetic_store(root)
    injector(root)

    before = _store_fingerprint(root)
    report = integrity.scan_integrity(root)
    after = _store_fingerprint(root)

    codes = {p["code"] for p in report["problems"]}
    return {
        "fault": name,
        "expected_code": expected_code,
        "detected": expected_code in codes,
        "store_unhealthy": report["healthy"] is False,
        "scan_read_only": before == after,
        "live_store_modified": report.get("live_store_modified", None),
    }


def run_harness(base: Path) -> dict[str, Any]:
    """Run every fault case plus a clean control. Metadata-only report."""
    base.mkdir(parents=True, exist_ok=True)

    # Clean control: a healthy synthetic store must report no problems.
    clean_root = base / "store-clean"
    if clean_root.exists():
        shutil.rmtree(clean_root)
    build_synthetic_store(clean_root)
    clean_before = _store_fingerprint(clean_root)
    clean_report = integrity.scan_integrity(clean_root)
    clean_after = _store_fingerprint(clean_root)
    control = {
        "fault": "clean_control",
        "expected_code": None,
        "detected": None,
        "store_unhealthy": clean_report["healthy"] is False,
        "healthy": clean_report["healthy"] is True,
        "scan_read_only": clean_before == clean_after,
    }

    cases = [
        _run_case(base, name, injector, code)
        for name, (injector, code) in _FAULTS.items()
    ]
    all_detected = all(c["detected"] for c in cases)
    all_unhealthy = all(c["store_unhealthy"] for c in cases)
    all_read_only = control["scan_read_only"] and all(c["scan_read_only"] for c in cases)
    return {
        "schema": 1,
        "harness": "store_integrity_v1",
        "synthetic_only": True,
        "control": control,
        "cases": cases,
        "fault_count": len(cases),
        "all_detected": all_detected,
        "all_unhealthy": all_unhealthy,
        "all_read_only": all_read_only,
        "control_healthy": control["healthy"],
        "overall_passed": (
            all_detected and all_unhealthy and all_read_only and control["healthy"]
        ),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Engram store integrity fault-injection harness (synthetic, read-only)",
        f"  control: {'healthy' if report['control_healthy'] else 'UNHEALTHY (unexpected)'}",
        f"  faults: {report['fault_count']}  all_detected={report['all_detected']}  "
        f"all_read_only={report['all_read_only']}",
    ]
    for case in report["cases"]:
        mark = "ok" if case["detected"] and case["store_unhealthy"] else "!!"
        lines.append(f"  [{mark}] {case['fault']} -> expect {case['expected_code']} "
                     f"(detected={case['detected']}, read_only={case['scan_read_only']})")
    lines.append(f"  overall: {'PASS' if report['overall_passed'] else 'FAIL'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic store integrity fault-injection harness.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp store dir and print its path.")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="engram-store-integrity-"))
    try:
        report = run_harness(base)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_text(report))
        if args.keep:
            print(f"Kept store base: {base}")
        return 0 if report["overall_passed"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
