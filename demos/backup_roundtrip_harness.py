"""Backup / restore / migration replay round-trip harness (Task 4, B+).

Proves the export → import → re-export loop is content-preserving and that the
*original* store is never touched, entirely on a copied/synthetic store in a
temp dir:

1. Seed a synthetic Engram store (fake identity + knowledge).
2. Fingerprint the original store on disk (sha256 of every file).
3. ``export_all`` → backup A.
4. Restore A into a *fresh* empty store via ``import_all(merge=False)``.
5. ``export_all`` from the restored store → backup B.
6. Assert the normalized content of A and B is equivalent (envelope timestamp
   stripped, lists canonically sorted), and the original fingerprint is
   unchanged.
7. Replay: re-import A into the restored store with ``merge=True`` and assert it
   adds nothing (idempotent migration replay).

Safety invariants (enforced):
- Writes only under the caller-provided temp base; never touches ``~/.engram``.
- ``ENGRAM_TEST=1`` is set so the fragmentation check stays quiet.
- The report is metadata-only: counts/booleans/digests, never knowledge bodies.

Run from the repo root::

    python demos/backup_roundtrip_harness.py            # human summary
    python demos/backup_roundtrip_harness.py --json      # machine-readable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("ENGRAM_TEST", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.core import Engram  # noqa: E402

# Fixed synthetic seed. Fake content only.
_SEED_LESSONS: list[dict[str, Any]] = [
    {"summary": "synthetic backup lesson one", "domain": "python", "detail": "alpha detail"},
    {"summary": "synthetic backup lesson two", "domain": "ops", "detail": "beta detail"},
]
_SEED_DECISIONS: list[dict[str, Any]] = [
    {"question": "which synthetic store format?", "choice": "json", "reasoning": "portable"},
]


def seed_store(root: Path) -> Engram:
    """Create a seeded synthetic Engram store under *root*."""
    eng = Engram(root=root)
    eng.update_profile({"role": "synthetic-tester", "language": "en"})
    for lesson in _SEED_LESSONS:
        eng.add_lesson(dict(lesson))
    for decision in _SEED_DECISIONS:
        eng.add_decision(dict(decision))
    return eng


def _store_fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return out


def _normalize_export(export: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize an export for content equivalence comparison.

    Strips the volatile envelope timestamp and sorts knowledge lists by their
    identity field so order differences don't read as content differences. Drops
    per-entry volatile bookkeeping (timestamps/access counts) that legitimately
    differs without changing meaning.
    """
    knowledge = export.get("knowledge", {}) if isinstance(export, dict) else {}
    _volatile = {
        "created_at",
        "timestamp",
        "last_updated",
        "last_accessed",
        "last_reviewed",
        "access_count",
    }

    def _entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in sorted(entry.items()) if k not in _volatile}

    def _sorted(entries: Any, key: str) -> list[dict[str, Any]]:
        rows = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
        return sorted((_entry(e) for e in rows), key=lambda e: str(e.get(key, "")))

    def _clean(value: Any) -> Any:
        """Recursively drop ``_``-prefixed and volatile bookkeeping keys.

        Internal provenance / ``updated_at`` stamps are not re-imported (import
        filters identity to allowed fields), so they are not content and must not
        read as a round-trip divergence.
        """
        if isinstance(value, dict):
            return {
                k: _clean(v)
                for k, v in value.items()
                if not str(k).startswith("_") and k not in _volatile and k != "updated_at"
            }
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    return {
        "identity": _clean(export.get("identity", {})),
        "lessons": _sorted(knowledge.get("lessons"), "summary"),
        "decisions": _sorted(knowledge.get("decisions"), "question"),
        "domains": knowledge.get("domains", {}),
        "playbooks": _sorted(knowledge.get("playbooks"), "title"),
        "tools": _sorted(
            (export.get("environment", {}) or {}).get("tools"), "name"
        ),
    }


def run_harness(base: Path) -> dict[str, Any]:
    """Run the full round-trip and return a metadata-only report."""
    base.mkdir(parents=True, exist_ok=True)
    original_root = base / "original"
    restored_root = base / "restored"
    backups = base / "backups"
    backups.mkdir(parents=True, exist_ok=True)

    # 1-2. Seed + fingerprint the original store.
    original = seed_store(original_root)
    fp_before = _store_fingerprint(original_root)

    # 3. Export A.
    backup_a = original.export_all(str(backups / "backup_a.json"))

    fp_after_export = _store_fingerprint(original_root)

    # 4. Restore into a fresh store (overwrite mode).
    restored = Engram(root=restored_root)
    restore_result = restored.import_all(backup_a, merge=False)

    # 5. Re-export B.
    backup_b = restored.export_all(str(backups / "backup_b.json"))

    export_a = json.loads(Path(backup_a).read_text(encoding="utf-8"))
    export_b = json.loads(Path(backup_b).read_text(encoding="utf-8"))
    norm_a = _normalize_export(export_a)
    norm_b = _normalize_export(export_b)
    content_equivalent = json.dumps(norm_a, ensure_ascii=False, sort_keys=True) == json.dumps(
        norm_b, ensure_ascii=False, sort_keys=True
    )

    # 6. Replay: re-import A into the restored store (merge) → adds nothing.
    replay = restored.import_all(backup_a, merge=True)
    replay_lessons = next(
        (s for s in replay.get("imported", []) if str(s).startswith("lessons")),
        "lessons(+0)",
    )
    replay_decisions = next(
        (s for s in replay.get("imported", []) if str(s).startswith("decisions")),
        "decisions(+0)",
    )
    replay_added_nothing = "(+0)" in replay_lessons and "(+0)" in replay_decisions

    # 7. Original store untouched across export + restore + replay.
    fp_final = _store_fingerprint(original_root)
    original_untouched = fp_before == fp_after_export == fp_final

    return {
        "schema": 1,
        "harness": "backup_roundtrip_v1",
        "synthetic_only": True,
        "temp_dir_only": True,
        "lesson_count": len(norm_a["lessons"]),
        "decision_count": len(norm_a["decisions"]),
        "restore_status": str(restore_result.get("status", "")),
        "content_equivalent": content_equivalent,
        "replay_added_nothing": replay_added_nothing,
        "original_untouched": original_untouched,
        "overall_passed": (
            content_equivalent and replay_added_nothing and original_untouched
        ),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Engram backup/restore round-trip harness (synthetic, temp-dir only)",
        f"  lessons: {report['lesson_count']}  decisions: {report['decision_count']}  "
        f"restore: {report['restore_status']}",
        f"  content_equivalent={report['content_equivalent']}  "
        f"replay_added_nothing={report['replay_added_nothing']}  "
        f"original_untouched={report['original_untouched']}",
        f"  overall: {'PASS' if report['overall_passed'] else 'FAIL'}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic backup/restore round-trip harness."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp base and print its path.")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="engram-backup-roundtrip-"))
    try:
        report = run_harness(base)
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
