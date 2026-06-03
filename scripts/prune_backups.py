#!/usr/bin/env python3
"""Prune Engram file-safety backups to a per-source-file retention cap.

``file_safety.backup_existing_file`` copies the prior version of every
Engram-owned JSON file on each write. Hot files (lessons.json, domains.json,
session_state.json) are rewritten constantly, so without a cap the backup
area grows to tens of thousands of files / multiple GB.

The live writer now enforces retention going forward; this script performs
the one-time (or periodic) cleanup of the existing backlog and can also clear
recovered ``.corrupt.*`` quarantine copies whose live data is intact.

Usage:
    python prune_backups.py --dry-run                 # report only
    python prune_backups.py                           # keep 10 per file
    python prune_backups.py --keep 5 --clean-corrupt  # also drop .corrupt copies
    python prune_backups.py --root /path/to/.engram

Backups are redundant copies; the most recent ``--keep`` per source file are
always retained as a safety margin.
"""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from pathlib import Path

# Strips the trailing ".<YYYYMMDD_HHMMSS_micros>.bak[.N]" stamp, leaving the
# stable "{name}.{path_hash}" group key shared by all backups of one file.
_STAMP_RE = re.compile(r"\.\d{8}_\d{6}_\d+\.bak(?:\.\d+)?$")


def _default_root() -> Path:
    env = os.environ.get("ENGRAM_DIR")
    if env:
        return Path(env).expanduser()
    home_engram = Path.home() / ".engram"
    if home_engram.is_dir():
        return home_engram
    return Path.home() / ".piia"


def _group_key(name: str) -> str | None:
    stripped = _STAMP_RE.sub("", name)
    return stripped if stripped != name else None


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def prune_backups(root: Path, keep: int, dry_run: bool) -> tuple[int, int]:
    """Return (files_removed, bytes_freed) across all backup scopes."""
    base = root / "backups" / "file_safety"
    if not base.is_dir():
        print(f"[backups] none found under {base}")
        return 0, 0

    removed = bytes_freed = 0
    for scope_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        # Capture (mtime, size) up front: the live server writes into this
        # directory concurrently, so files can change between listing and use.
        groups: dict[str, list[tuple[float, int, Path]]] = defaultdict(list)
        for entry in scope_dir.iterdir():
            key = _group_key(entry.name)
            if key is None:
                continue
            try:
                st = entry.stat()
            except OSError:
                continue  # vanished mid-scan; skip
            groups[key].append((st.st_mtime, st.st_size, entry))

        for key, files in sorted(groups.items()):
            files.sort(key=lambda t: t[0], reverse=True)  # newest first
            stale = files[keep:]
            if not stale:
                continue
            size = sum(s for _, s, _ in stale)
            print(
                f"[{scope_dir.name}] {key}: {len(files)} -> {keep} "
                f"(drop {len(stale)}, {_human(size)})"
            )
            bytes_freed += size
            removed += len(stale)
            if not dry_run:
                for _, s, p in stale:
                    try:
                        p.unlink()
                    except OSError as exc:
                        print(f"  ! could not remove {p.name}: {exc}")
                        removed -= 1
                        bytes_freed -= s
    return removed, bytes_freed


def clean_corrupt(root: Path, dry_run: bool) -> tuple[int, int]:
    """Remove recovered .corrupt quarantine copies under knowledge/ etc."""
    removed = bytes_freed = 0
    for corrupt in root.rglob("*.corrupt.*.json"):
        if not corrupt.is_file():
            continue
        size = corrupt.stat().st_size
        print(f"[corrupt] {corrupt.relative_to(root)} ({_human(size)})")
        bytes_freed += size
        removed += 1
        if not dry_run:
            try:
                corrupt.unlink()
            except OSError as exc:
                print(f"  ! could not remove {corrupt.name}: {exc}")
                removed -= 1
                bytes_freed -= size
    return removed, bytes_freed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Engram data root")
    parser.add_argument(
        "--keep", type=int, default=10, help="backups to retain per source file"
    )
    parser.add_argument(
        "--clean-corrupt",
        action="store_true",
        help="also delete recovered .corrupt.* quarantine copies",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only")
    args = parser.parse_args()

    root = (args.root or _default_root()).expanduser().resolve()
    keep = max(1, args.keep)
    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"== prune_backups [{mode}] root={root} keep={keep} ==")

    total_removed = total_freed = 0
    r, f = prune_backups(root, keep, args.dry_run)
    total_removed += r
    total_freed += f
    if args.clean_corrupt:
        r, f = clean_corrupt(root, args.dry_run)
        total_removed += r
        total_freed += f

    verb = "would free" if args.dry_run else "freed"
    print(f"\n== {mode}: {total_removed} files, {verb} {_human(total_freed)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
