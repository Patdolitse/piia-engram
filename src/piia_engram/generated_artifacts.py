"""Store-local inventory of generated files, including external destinations.

Only absolute target paths are recorded, never generated bodies. Register before
publishing so an interrupted write cannot leave an untracked snapshot. Deletion
is the invalidation policy: offline consumers must regenerate a missing file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .file_safety import atomic_write_text
from .storage import hold_directory_lock

MANIFEST = "generated_artifacts.json"


def _target(root: Path, path: Path) -> Path:
    from .isolated_store import LIMITS_FILE, MARKER, REPLAY_MARKER_FILE

    root, path = root.resolve(), path.absolute()
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("generated_artifact_target_changed")
    if path.is_relative_to(root):
        parts = path.relative_to(root).parts
        if (not parts or parts[0] in {"knowledge", "identity", "recovery", "backups"}
                or (len(parts) == 1 and parts[0] in {
                    MANIFEST, "audit.log", "replay_row_repair.pending.json",
                    MARKER, LIMITS_FILE, REPLAY_MARKER_FILE, ".engram-write.lock",
                })):
            raise ValueError("generated_artifact_reserved_target")
    return path


def recorded_targets(root: Path) -> list[Path]:
    """Zero-write read; malformed inventories are never silently discarded."""
    path = Path(root) / MANIFEST
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("generated_artifact_manifest_link")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("targets"), list)
            or any(not isinstance(p, str) or not Path(p).is_absolute() for p in data["targets"])):
        raise ValueError("generated_artifact_manifest_invalid")
    return [Path(p) for p in sorted(set(data["targets"]))]


def write_generated_text(root: Path, path: Path, text: str | Callable[[], str]) -> Path:
    """Publish a UTF-8 artifact only after its durable inventory registration."""
    from .isolated_store import PRODUCTION, _mode_context, root_mode

    root, original = Path(root), Path(path)
    path = _target(root, original.resolve())
    with hold_directory_lock(root):
        marker, _, _ = _mode_context(root)
        root_mode(root, (marker or {}).get("mode", PRODUCTION), audit_refusal=False)
        content = text() if callable(text) else text
        paths = {str(p) for p in recorded_targets(root)} | {str(path)}
        atomic_write_text(root / MANIFEST, json.dumps(
            {"version": 1, "targets": sorted(paths)}, ensure_ascii=False, indent=2) + "\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
    return original


def invalidate_generated(root: Path) -> dict:
    """Delete all recorded files; retain paths so failures can be retried.

    A changed alias or unwritable destination is reported by path and error
    class, without reading any body. Callers decide whether to remain closed.
    """
    removed, failed = [], []
    with hold_directory_lock(root):
        for path in recorded_targets(root):
            try:
                _target(root, path)
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
            except (OSError, ValueError) as exc:
                failed.append({"target": str(path), "error": type(exc).__name__,
                               "outside_store": not path.is_relative_to(Path(root).resolve())})
    return {"invalidated_targets": removed, "invalidation_failures": failed}
