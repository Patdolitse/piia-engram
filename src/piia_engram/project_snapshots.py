"""Fail-closed schema validation and explicit, backed-up snapshot migration."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path

SCHEMA = "project_snapshot.v2"
NESTED_KEYS = ("snapshot", "data")

class SnapshotMigrationRequired(ValueError):
    code = "migration_required"
    def __init__(self, reason):
        self.reason = reason
        super().__init__("migration_required: " + reason + "; run engram migrate-project locally")


def condition(data):
    if not isinstance(data, dict):
        return "snapshot must be an object"
    if any(k in data for k in NESTED_KEYS):
        return "nested or mixed snapshot layout"
    if data.get("schema") not in (None, SCHEMA):
        return "unsupported snapshot schema"
    if "schema_version" in data:
        return "unexpected schema_version field"
    for key in ("current_state", "checkpoint"):
        if key in data and not isinstance(data[key], dict):
            return key + " must be an object"
    if "checkpoint_history" in data and not isinstance(data["checkpoint_history"], list):
        return "checkpoint_history must be an array"
    return ""


def require_writable(data):
    reason = condition(data)
    if reason:
        raise SnapshotMigrationRequired(reason)


def read_raw(path):
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise SnapshotMigrationRequired("snapshot cannot be decoded") from exc


def read_snapshot(path):
    try:
        data = read_raw(path)
        reason = condition(data)
    except SnapshotMigrationRequired as exc:
        data, reason = {}, exc.reason
    if not reason:
        return data
    result = deepcopy(data) if isinstance(data, dict) else {}
    # Expose readable legacy metadata, keeping the original nested body available.
    for key in NESTED_KEYS:
        body = result.get(key)
        if isinstance(body, dict):
            for field, value in body.items():
                result.setdefault(field, value)
    result["migration"] = {"error": "migration_required", "reason": reason, "read_only": True,
                           "hint": "Run engram migrate-project <project> locally; preview before applying."}
    return result


def migrate(root, path, *, apply=False, prefer=""):
    from .file_safety import backup_existing_file
    from .storage import _update_json, DataCorruptionError
    if prefer not in ("", "nested", "top-level"):
        return {"error": "migration_required", "reason": "invalid conflict preference"}
    backup = None
    try:
        raw = read_raw(path)
    except SnapshotMigrationRequired as exc:
        if apply:
            backup = backup_existing_file(root, path, scope="project_snapshot", tool="migrate-project")
        return {"error": exc.code, "reason": exc.reason, "backup": str(backup) if backup else ""}
    def flatten(data):
        if not isinstance(data, dict):
            raise SnapshotMigrationRequired("snapshot must be an object")
        nested_keys = [k for k in NESTED_KEYS if k in data]
        if len(nested_keys) > 1 or any(not isinstance(data[k], dict) for k in nested_keys):
            raise SnapshotMigrationRequired("ambiguous or invalid nested objects")
        if data.get("schema") not in (None, SCHEMA, "project_snapshot.v1"):
            raise SnapshotMigrationRequired("unsupported snapshot schema; restore a valid backup")
        top = {k:v for k,v in data.items() if k not in (*NESTED_KEYS, "schema", "schema_version", "migration")}
        nested = deepcopy(data[nested_keys[0]]) if nested_keys else {}
        conflicts = sorted(k for k in nested if k in top and nested[k] != top[k])
        if conflicts and not prefer:
            raise SnapshotMigrationRequired("conflicting fields: " + ", ".join(conflicts))
        merged = {**top, **nested} if prefer == "nested" else {**nested, **top}
        merged["schema"] = SCHEMA
        require_writable(merged)
        return merged
    if not apply:
        try:
            candidate = flatten(raw)
            return {"status": "preview", "migration_required": bool(condition(raw)), "conflicts": [], "read_only": True}
        except SnapshotMigrationRequired as exc:
            return {"status": "preview", "error": exc.code, "reason": exc.reason, "read_only": True}
    def mutate(data):
        nonlocal backup
        candidate = flatten(data)
        backup = backup_existing_file(root, path, scope="project_snapshot", tool="migrate-project")
        if path.is_file() and backup is None:
            raise SnapshotMigrationRequired("backup unavailable")
        return candidate
    try:
        _update_json(path, mutate)
    except (SnapshotMigrationRequired, DataCorruptionError) as exc:
        return {"error": "migration_required", "reason": getattr(exc, "reason", "snapshot became corrupt before the write lock")}
    return {"status": "migrated", "backup": str(backup) if backup else ""}
