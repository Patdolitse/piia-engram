"""Metadata-only store footprint report and owner-invoked maintenance.

Read side is content-free by design: sizes, counts, and warning strings only —
safe to surface in doctor output and public-style summaries.
"""
from __future__ import annotations

from pathlib import Path

_MB = 1024 * 1024
LOG_WARN_BYTES = 64 * _MB
BACKUPS_WARN_BYTES = 500 * _MB


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        pass
    return 0


def store_footprint(
    root: Path,
    *,
    log_warn_bytes: int = LOG_WARN_BYTES,
    backups_warn_bytes: int = BACKUPS_WARN_BYTES,
) -> dict:
    root = Path(root)
    ledger = _size(root / "file_safety_ledger.jsonl")
    audit = _size(root / "audit.log")
    backups = _size(root / "backups")
    contexts = _size(root / "contexts")
    report = {
        "ledger_bytes": ledger,
        "audit_bytes": audit,
        "backups_bytes": backups,
        "contexts_bytes": contexts,
        "total_bytes": _size(root),
        "warnings": [],
    }
    if ledger > log_warn_bytes:
        report["warnings"].append(
            f"file_safety_ledger.jsonl is {ledger // _MB}MB; run 'engram doctor --fix' to rotate"
        )
    if audit > log_warn_bytes:
        report["warnings"].append(
            f"audit.log is {audit // _MB}MB; run 'engram doctor --fix' to rotate"
        )
    if backups > backups_warn_bytes:
        report["warnings"].append(
            f"backups/ holds {backups // _MB}MB; run 'engram doctor --fix' to prune"
        )
    return report


def activation_status(root: Path) -> dict:
    """Metadata-only activation snapshot: bootstrapped? any durable memory?"""
    root = Path(root)

    def _has_items(p: Path) -> bool:
        try:
            return p.is_file() and p.stat().st_size > 4  # more than "[]"
        except OSError:
            return False

    return {
        "bootstrapped": (root / ".bootstrap_done").is_file(),
        "has_memory": (
            _has_items(root / "knowledge" / "lessons.json")
            or _has_items(root / "knowledge" / "decisions.json")
        ),
    }


def apply_store_maintenance(root: Path) -> list[str]:
    """Owner-invoked cleanup: rotate oversized logs, prune upgrade backups."""
    from . import audit as audit_mod
    from . import file_safety

    root = Path(root)
    actions: list[str] = []
    if file_safety.rotate_if_oversized(
        root / "file_safety_ledger.jsonl", file_safety.LEDGER_MAX_BYTES
    ):
        actions.append("rotated file_safety_ledger.jsonl")
    if file_safety.rotate_if_oversized(root / "audit.log", audit_mod.AUDIT_MAX_BYTES):
        actions.append("rotated audit.log")

    from .core import Engram

    Engram(root=root)._prune_backups()
    actions.append("pruned upgrade backups to newest-per-version cap")
    return actions
