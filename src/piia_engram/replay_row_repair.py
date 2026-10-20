"""Owner-local quarantine of accidentally pasted replay rows; never an MCP tool."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .atomic_replace import replace_with_retry
from .isolated_store import (
    GuardRefused, LIMITS_FILE, MARKER, REPLAY_MARKER_FILE, RECEIPTS_FILE,
    _initial_receipt, _mode_context, _root_id, carries_replay_marker,
    stored_replay_findings,
)
from .storage import _engram_root, hold_directory_lock

PENDING = "replay_row_repair.pending.json"
KNOWLEDGE_FILES = (
    "knowledge/lessons.json", "knowledge/decisions.json",
    "knowledge/overflow_archive/lessons.jsonl",
    "knowledge/overflow_archive/decisions.jsonl",
)


def _ordinary_only(root: Path) -> None:
    """Reject all recognized isolation evidence, including custom sibling ledgers.

    A lost external locator cannot be reconstructed globally. In addition to
    configured and conventional ledgers, inspect sibling ledger directories;
    bind evidence by directory identity, never by a guessed directory name.
    Row provenance itself always blocks normal use, regardless of ledger layout.
    """
    from .review_boundary import mcp_origin

    if mcp_origin():
        raise GuardRefused("local_repair_only")
    _, audit_dir, isolated = _mode_context(root)
    if isolated or any((root / name).exists() for name in (MARKER, LIMITS_FILE, REPLAY_MARKER_FILE)):
        raise GuardRefused("repair_isolated_root")
    candidates = {audit_dir, root.with_name("receipts")}
    candidates.update(p.parent for p in root.parent.glob(f"*/{RECEIPTS_FILE}"))
    for directory in candidates:
        try:
            initial = _initial_receipt(directory, required=False)
        except GuardRefused:
            # A damaged conventional guard ledger is unsafe; an unrelated
            # sibling's unreadable ledger is not evidence about this root.
            if directory == audit_dir:
                raise
            continue
        if initial and initial.get("root_id") == _root_id(root):
            raise GuardRefused("repair_isolated_root")


def _local_path(root: Path, relative: str) -> Path:
    path = root / relative
    # No symlink/junction traversal even when the final target is inside root.
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise GuardRefused("repair_link_refused")
    if not path.resolve().is_relative_to(root.resolve()):
        raise GuardRefused("repair_path_refused")
    return path


def _plan(root: Path) -> list[tuple[str, bytes, bytes, list]]:
    plan = []
    # Parse every file before making any change, even an unmarked file.
    for relative in KNOWLEDGE_FILES:
        path = _local_path(root, relative)
        if not path.exists():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        if path.suffix == ".jsonl":
            kept, marked = [], []
            for line in text.splitlines(keepends=True):
                if line.strip():
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected knowledge object")
                    if carries_replay_marker(row):
                        marked.append(row)
                        continue
                kept.append(line)
            replacement = "".join(kept).encode("utf-8")
        else:
            rows = json.loads(text)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("expected knowledge list")
            marked = [row for row in rows if carries_replay_marker(row)]
            kept = [row for row in rows if not carries_replay_marker(row)]
            replacement = (json.dumps(kept, ensure_ascii=False, indent=2) + "\r\n").encode("utf-8")
        if marked:
            plan.append((relative, raw, replacement, marked))
    return plan


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def _derived(root: Path) -> list[Path]:
    # Only rebuildable, store-owned artifacts. Portable exports deliberately
    # written outside the store are Owner-managed copies, not searched here.
    paths = [root / name for name in (
        "quick_context.md", "search_index.db", "search_index.db-wal", "search_index.db-shm",
    )]
    exports = root / "exports"
    for pattern in ("identity_card.md", "knowledge_report_*.md", "review_*.html"):
        paths.extend(exports.glob(pattern))
    # refresh_quick_context(target=...) can use another store-local name.
    for path in root.rglob("*.md"):
        if path.relative_to(root).parts[0] in {"recovery", "knowledge", "backups"}:
            continue
        _local_path(root, path.relative_to(root).as_posix())
        with path.open("rb") as source:
            if source.read(160).startswith(b"<!-- Engram quick_context snapshot"):
                paths.append(path)
    return sorted({p for p in paths if p.exists()})


def repair(root: Path, *, apply: bool = False) -> dict:
    """Dry-run is zero-write; apply is explicitly local and preserves provenance."""
    root = Path(root).absolute()
    _ordinary_only(root)
    plan = _plan(root)
    files = {relative: len(rows) for relative, _, _, rows in plan}
    if not apply:
        return {"files": files, "read_only": True, "changed": False}
    if not plan and not (root / PENDING).exists():
        return {"files": {}, "changed": False}
    # Preflight generated paths and all recovery destinations before writing.
    artifacts = _derived(root)
    for path in artifacts:
        _local_path(root, path.relative_to(root).as_posix())
    _local_path(root, "audit.log")
    _local_path(root, PENDING)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    recovery = _local_path(root, f"recovery/replay-rows/{stamp}")
    with ExitStack() as stack:
        for directory in sorted({(root / relative).parent for relative in KNOWLEDGE_FILES
                                 if (root / relative).parent.exists()}):
            stack.enter_context(hold_directory_lock(directory))
        _ordinary_only(root)
        # Re-read under the same directory locks used by normal knowledge writes.
        plan = _plan(root)
        files = {relative: len(rows) for relative, _, _, rows in plan}
        for relative, raw, _, _ in plan:
            _write_new(recovery / (relative + ".bak"), raw)
        quarantined = [json.dumps({"file": relative, "row": row}, ensure_ascii=False)
                       for relative, _, _, rows in plan for row in rows]
        _write_new(recovery / "quarantine.jsonl", ("\r\n".join(quarantined) + "\r\n").encode("utf-8"))
        # An interrupted invalidation/rewrite must remain closed until rerun.
        if not (root / PENDING).exists():
            _write_new(root / PENDING, b'{"repair":"replay-rows"}\r\n')
        for path in artifacts:
            path.unlink(missing_ok=True)
        for relative, _, replacement, _ in plan:
            path = root / relative
            temp = path.with_name(path.name + ".repair-" + uuid.uuid4().hex + ".tmp")
            try:
                _write_new(temp, replacement)
                replace_with_retry(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        event = {"action": "replay_rows_repaired", "ts": stamp, "files": files,
                 "quarantined": sum(files.values()), "invalidated": len(artifacts)}
        with (root / "audit.log").open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(event, ensure_ascii=False) + "\n")
            audit.flush()
            os.fsync(audit.fileno())
        (root / PENDING).unlink()
    return {"files": files, "changed": True, "quarantined": sum(files.values())}


def run_cli(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="engram repair")
    parser.add_argument("target", choices=["replay-rows"])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    options = parser.parse_args(args)
    root = Path(_engram_root())
    try:
        result = repair(root, apply=options.apply)
    except (GuardRefused, OSError, ValueError) as exc:
        result = {"error": exc.code if isinstance(exc, GuardRefused) else type(exc).__name__,
                  "hint": "Run engram doctor; no unreadable knowledge file was rewritten.",
                  "stored_replay": stored_replay_findings(root)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
