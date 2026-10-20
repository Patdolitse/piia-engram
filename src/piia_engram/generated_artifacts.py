"""Informational generated-file records, never authority to modify a target.

Only an explicit generator call publishes to its caller's chosen destination.
Repair reports old snapshots without deleting or rewriting any recorded target.
Canonical regeneration uses a fixed list independently of these records.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sys
from typing import Callable

from .file_safety import atomic_write_text
from .storage import hold_directory_lock

MANIFEST = "generated_artifacts.json"
CANONICAL_TEXT = ("quick_context.md", "exports/identity_card.md")
CANONICAL_METADATA = ("reports/status.html", "reports/dashboard.html")
CANONICAL_INDEX = ("search_index.db", "search_index.db-wal", "search_index.db-shm", "search_index.db-journal")
SOURCE_AREAS = frozenset({
    "knowledge", "identity", "playbooks", "recovery", "backups", "ledgers",
    "receipts", "projects", "environment", "context", "contexts", "logs",
    "daily", "operations", "portraits", "governance",
})
STALE_MESSAGE = "stale generated file - regenerate or delete it yourself"
UNVERIFIED_MESSAGE = "not verified, left untouched"
SOURCE_FILES = frozenset({
    MANIFEST, "audit.log", "replay_row_repair.pending.json",
    "isolated_store_root.json", "isolated_store_limits.json",
    "replay_experience.marker", "receipts.jsonl", "refusals.jsonl",
    ".engram-write.lock", ".corpus_salt", ".backup_state.json",
    ".bootstrap_done", ".update_check.json", "session_state.json",
    "schema_version", "schema_version.json", "approval_mode.json",
    "shared_instructions.md", "file_safety_ledger.jsonl", "file_safety_ledger.jsonl.1",
    "audit.log.1", "telemetry.log", "telemetry.log.1", "telemetry_config.json",
    "beta_events.jsonl", "beta_events.jsonl.1", "setup_report.jsonl",
})


def _windows_path(value: str) -> str:
    value = value.replace("\\", "/")
    if value[:8].casefold() in {"//?/unc/", "//./unc/"}:
        value = "//" + value[8:]
    elif value.startswith(("//?/", "//./")):
        value = value[4:]
    return ntpath.normpath(value).replace("\\", "/").casefold()


def _path_key(path: Path) -> str:
    if os.name == "nt":
        return _windows_path(str(Path(_windows_path(str(path))).resolve()))
    value = str(path.resolve()).replace(os.sep, "/")
    # Default macOS volumes and Windows compare paths without case.
    return value.casefold() if os.name == "nt" or sys.platform == "darwin" else value


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except (OSError, ValueError):
        return False


def _source_paths(root: Path) -> list[Path]:
    paths = [root, *(root / name for name in SOURCE_FILES)]
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if base == root:
            dirs[:] = [name for name in dirs if name.casefold() in SOURCE_AREAS]
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()
                   and not (hasattr(base / name, "is_junction") and (base / name).is_junction())]
        if base != root:
            paths.append(base)
            paths.extend(base / name for name in (*dirs, *files))
    return paths


def _source_target(root: Path, path: Path, sources: list[Path] | None = None) -> bool:
    base, target = _path_key(root).rstrip("/"), _path_key(path)
    if target == base:
        return True
    if target.startswith(base + "/"):
        parts = target[len(base) + 1:].split("/")
        if parts[0] in SOURCE_AREAS or (len(parts) == 1 and parts[0] in SOURCE_FILES):
            return True
    return path.exists() and any(_same_file(path, source) for source in (
        sources if sources is not None else _source_paths(root)))


def _canonical_target(root: Path, path: Path, names: tuple[str, ...]) -> bool:
    key = _path_key(path)
    return any(key == _path_key(root / name) or _same_file(path, root / name) for name in names)


def _manifest_path(root: Path) -> Path:
    path = root / MANIFEST
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("generated_artifact_manifest_link")
    return path


def _inventory(root: Path) -> tuple[list[dict], list[dict]]:
    """Old path-only records are unverified; bad metadata is diagnostic."""
    try:
        data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as exc:
        return [], [{"target": str(root / MANIFEST), "status": "invalid",
                     "reason": type(exc).__name__, "message": UNVERIFIED_MESSAGE}]
    if (not isinstance(data, dict) or data.get("version") not in (1, 2)
            or not isinstance(data.get("targets"), list)):
        return [], [{"target": str(root / MANIFEST), "status": "invalid",
                     "reason": "invalid_inventory", "message": UNVERIFIED_MESSAGE}]
    records, invalid = [], []
    for entry in data["targets"]:
        record = {"path": entry, "sha256": None, "generated_at": None} if isinstance(entry, str) else entry
        if (not isinstance(record, dict) or not isinstance(record.get("path"), str)
                or not Path(record["path"]).is_absolute()):
            invalid.append({"target": record.get("path", "") if isinstance(record, dict) else "",
                            "status": "invalid", "reason": "invalid_record",
                            "message": UNVERIFIED_MESSAGE})
            continue
        records.append({key: record.get(key) for key in ("path", "sha256", "generated_at")})
    return records, invalid


def recorded_targets(root: Path) -> list[Path]:
    """Compatibility read helper; callers must never mutate returned paths."""
    records, _ = _inventory(Path(root))
    return [Path(p) for p in sorted({record["path"] for record in records})]


def write_generated_text(root: Path, path: Path, text: str | Callable[[], str]) -> Path:
    """Publish to the explicit target, then record the exact published hash.

    Failed publication does not register never-written content. Existing
    records are data only and are never used as publication destinations.
    """
    from .isolated_store import PRODUCTION, _mode_context, root_mode

    root, original = Path(root), Path(path)
    target = original.resolve()
    if _source_target(root, target):
        raise ValueError("generated_artifact_reserved_target")
    with hold_directory_lock(root):
        manifest = _manifest_path(root)
        marker, _, _ = _mode_context(root)
        root_mode(root, (marker or {}).get("mode", PRODUCTION), audit_refusal=False)
        content = text() if callable(text) else text
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        if _canonical_target(root, target, (*CANONICAL_TEXT, *CANONICAL_METADATA)):
            return original
        # atomic_write_text uses UTF-8 and platform newline translation.
        digest = hashlib.sha256(content.replace("\n", os.linesep).encode("utf-8")).hexdigest()
        records, _ = _inventory(root)
        retained = []
        for record in records:
            try:
                if _path_key(Path(record["path"])) != _path_key(target):
                    retained.append(record)
            except (OSError, ValueError, RuntimeError):
                continue
        retained.append({"path": str(target), "sha256": digest,
                         "generated_at": datetime.now(timezone.utc).isoformat()})
        atomic_write_text(manifest, json.dumps(
            {"version": 2, "targets": retained}, ensure_ascii=False, indent=2) + "\n")
    return original


def _legacy_header(path: Path) -> bool:
    with path.open("rb") as source:
        header = source.read(1600)
    text = header.decode("utf-8", errors="replace").replace("\r\n", "\n")
    name = path.name.lstrip(".")
    return (header.startswith(b"<!-- Engram quick_context snapshot")
            or b"_Auto-generated by Engram_" in header
            or "_由 Engram 自动生成_".encode("utf-8") in header
            or (name.startswith("knowledge_report_")
                and text.startswith("# 个人知识报告\n生成时间："))
            or (name.startswith("review_") and text.startswith("<!DOCTYPE html>")
                and ("<title>Engram Knowledge Review</title>" in text
                     or "<title>Engram 知识审查</title>" in text)))


def inspect_generated(root: Path) -> dict:
    """Zero-write report. Its result is never a mutation plan."""
    root = Path(root)
    records, report = _inventory(root)
    seen = set()
    sources = _source_paths(root)
    canonical = {_path_key(root / name) for name in (*CANONICAL_TEXT, *CANONICAL_METADATA, *CANONICAL_INDEX)}
    for record in records:
        path = Path(record["path"])
        item = {"target": record["path"], "status": "not_verified", "message": UNVERIFIED_MESSAGE}
        try:
            key = _path_key(path)
            seen.add(key)
            if _source_target(root, path, sources):
                item.update(status="invalid", reason="reserved_store_source")
            elif _canonical_target(root, path, (*CANONICAL_TEXT, *CANONICAL_METADATA, *CANONICAL_INDEX)):
                item.update(status="invalid", reason="canonical_artifact_managed_separately")
            elif not path.is_file():
                item["reason"] = "missing_or_not_file"
            elif not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                if (key.startswith(_path_key(root).rstrip("/") + "/")
                        and _legacy_header(path)):
                    item.update(status="stale_generated_file", verification="legacy_header", message=STALE_MESSAGE)
                else:
                    item["reason"] = "no_recorded_hash"
            else:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(65536), b""):
                        digest.update(block)
                if digest.hexdigest() == record["sha256"]:
                    item.update(status="stale_generated_file", verification="sha256", message=STALE_MESSAGE)
                else:
                    item["reason"] = "content_changed"
        except (OSError, ValueError, RuntimeError) as exc:
            item["reason"] = type(exc).__name__
        report.append(item)
    # Legacy output locations include hidden snapshots. Never traverse source
    # areas or directory links; require Engram's own header, not an extension.
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = [name for name in dirs if not _source_target(root, base / name, sources)
                   and not (base / name).is_symlink()
                   and not (hasattr(base / name, "is_junction") and (base / name).is_junction())]
        for name in sorted(files):
            path = base / name
            try:
                key = _path_key(path)
                if key in seen or key in canonical or _source_target(root, path, sources):
                    continue
                if _legacy_header(path):
                    report.append({"target": str(path), "status": "stale_generated_file",
                                   "verification": "legacy_header", "message": STALE_MESSAGE})
            except (OSError, ValueError, RuntimeError):
                continue  # unrecorded unreadable files cannot be identified
    return {"generated_files": report}
