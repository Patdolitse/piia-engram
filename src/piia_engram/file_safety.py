"""Central file-safety helpers for Engram-owned and external writes."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Literal

PathScope = Literal["engram_root", "external"]


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def classify_path(root: Path, path: Path) -> PathScope:
    """Classify a path relative to the active Engram root."""
    root_resolved = _resolve(root)
    path_resolved = _resolve(path)
    try:
        path_resolved.relative_to(root_resolved)
        return "engram_root"
    except ValueError:
        return "external"


def path_hash(path: Path) -> str:
    """Short stable hash for local path references."""
    return hashlib.sha256(str(_resolve(path)).encode("utf-8")).hexdigest()[:12]


def redact_path(root: Path, path: Path) -> str:
    """Return a local-diagnostic path label without leaking external paths."""
    root_resolved = _resolve(root)
    path_resolved = _resolve(path)
    try:
        rel = path_resolved.relative_to(root_resolved)
        rel_text = rel.as_posix()
        return "<engram-root>" if not rel_text else f"<engram-root>/{rel_text}"
    except ValueError:
        return f"<external:{path_hash(path)}>"


def _ledger_path(root: Path) -> Path:
    return Path(root) / "file_safety_ledger.jsonl"


def _append_ledger(
    root: Path,
    *,
    operation: str,
    scope: str,
    tool: str,
    path: Path,
    backup_path: Path | None,
    result: str,
) -> None:
    ledger = _ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "scope": scope,
        "tool": tool,
        "path": redact_path(root, path),
        "path_sha256_12": path_hash(path),
        "backup_path": redact_path(root, backup_path) if backup_path else "",
        "result": result,
    }
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_ledger_entries(root: Path) -> list[dict]:
    """Read metadata-only file safety ledger entries."""
    ledger = _ledger_path(root)
    if not ledger.is_file():
        return []
    entries: list[dict] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entries.append(json.loads(line))
    return entries


def backup_existing_file(
    root: Path,
    path: Path,
    *,
    scope: str,
    tool: str,
) -> Path | None:
    """Back up an existing file under the Engram root backup area."""
    path = Path(path)
    if not path.is_file():
        return None
    backup_dir = Path(root) / "backups" / "file_safety" / scope
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{path.name}.{path_hash(path)}.{_utc_stamp()}.bak"
    backup_path = backup_dir / safe_name
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{safe_name}.{counter}"
        counter += 1
    shutil.copy2(path, backup_path)
    return backup_path


def record_file_write(
    root: Path,
    path: Path,
    *,
    scope: str,
    tool: str,
    backup_path: Path | None,
    result: str = "success",
) -> None:
    """Record a metadata-only file write in the file safety ledger."""
    _append_ledger(
        root,
        operation="write",
        scope=scope,
        tool=tool,
        path=path,
        backup_path=backup_path,
        result=result,
    )


def write_engram_text(root: Path, path: Path, text: str, *, tool: str) -> Path | None:
    """Write an Engram-owned text file, backing up changed existing files."""
    root = Path(root)
    path = Path(path)
    if classify_path(root, path) != "engram_root":
        raise PermissionError(f"Engram writes must stay inside ENGRAM_DIR: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing == text:
        return None
    backup_path = backup_existing_file(root, path, scope="engram_root", tool=tool)
    path.write_text(text, encoding="utf-8")
    _append_ledger(
        root,
        operation="write",
        scope="engram_root",
        tool=tool,
        path=path,
        backup_path=backup_path,
        result="success",
    )
    return backup_path


def write_engram_json(
    root: Path,
    path: Path,
    data: dict | list,
    *,
    tool: str,
) -> Path | None:
    """Write an Engram-owned JSON file with stable formatting."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return write_engram_text(root, path, text, tool=tool)


def write_external_config_text(
    root: Path,
    path: Path,
    text: str,
    *,
    tool: str,
    authorized: bool,
) -> Path | None:
    """Write an external config only after explicit user authorization."""
    root = Path(root)
    path = Path(path)
    if not authorized:
        raise PermissionError(f"external file write requires explicit authorization: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing == text:
        return None
    backup_path = backup_existing_file(root, path, scope="external", tool=tool)
    path.write_text(text, encoding="utf-8")
    _append_ledger(
        root,
        operation="write",
        scope="external",
        tool=tool,
        path=path,
        backup_path=backup_path,
        result="success",
    )
    return backup_path
