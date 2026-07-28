"""Session closeout budget and operation-status helpers."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import portalocker

try:
    from .storage import _now_iso, _project_id, _read_json, _write_json
except ImportError:  # plain-script mode
    from storage import _now_iso, _project_id, _read_json, _write_json  # type: ignore[no-redef]


DEFAULT_WRAP_UP_MAX_MS = 30_000


def configured_wrap_up_budget_ms() -> int:
    raw = os.environ.get("ENGRAM_WRAP_UP_MAX_MS", "").strip()
    if not raw:
        return DEFAULT_WRAP_UP_MAX_MS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WRAP_UP_MAX_MS
    return max(1, min(value, 300_000))


def configured_closeout_mode() -> str:
    raw = os.environ.get("ENGRAM_WRAP_UP_MODE", "").strip().lower()
    return "fast" if raw == "fast" else "standard"


def elapsed_ms(start: float) -> int:
    return max(0, int((perf_counter() - start) * 1000))


def budget_exhausted(*, total_start: float, budget_ms: int) -> bool:
    return elapsed_ms(total_start) >= budget_ms


def budget_metadata(*, total_start: float, budget_ms: int) -> dict[str, object]:
    used = elapsed_ms(total_start)
    return {
        "status": "exhausted" if used >= budget_ms else "ok",
        "budget_ms": budget_ms,
        "used_ms": used,
    }


def skipped_stage(reason: str) -> dict[str, str]:
    return {"status": "skipped", "reason": reason}


_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,96}$")
_TERMINAL_STATUSES = frozenset({"completed", "partial_complete", "failed"})
RUNNING_STALE_GRACE_MS = 60_000


def wrap_up_operations_dir(root: Path) -> Path:
    return root / "operations" / "wrap_up_session"


def _operation_id_for_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(
        ("wrap_up_session:v1:" + idempotency_key).encode("utf-8")
    ).hexdigest()
    return f"wrap-{digest[:32]}"


def new_wrap_up_operation_id(idempotency_key: str = "") -> str:
    key = (idempotency_key or "").strip()
    if key:
        return _operation_id_for_key(key)
    return f"wrap-{uuid.uuid4().hex}"


def validate_wrap_up_operation_id(operation_id: str) -> str:
    value = (operation_id or "").strip()
    if not value or not _OPERATION_ID_RE.fullmatch(value):
        raise ValueError("invalid operation_id")
    return value


def resolve_wrap_up_operation_id(
    *,
    operation_id: str = "",
    idempotency_key: str = "",
) -> str:
    """Resolve a status lookup without requiring a timed-out call's response."""
    raw_operation_id = (operation_id or "").strip()
    key = (idempotency_key or "").strip()
    if not raw_operation_id and not key:
        raise ValueError("operation_id or idempotency_key is required")

    resolved = validate_wrap_up_operation_id(raw_operation_id) if raw_operation_id else ""
    if key:
        keyed_id = _operation_id_for_key(key)
        if resolved and resolved != keyed_id:
            raise ValueError("operation_id does not match idempotency_key")
        resolved = keyed_id
    return resolved


def wrap_up_operation_path(root: Path, operation_id: str) -> Path:
    return wrap_up_operations_dir(root) / f"{validate_wrap_up_operation_id(operation_id)}.json"


def read_wrap_up_operation(root: Path, operation_id: str) -> dict[str, Any]:
    path = wrap_up_operation_path(root, operation_id)
    data = _read_json(path, allow_corrupt=True)
    return data if isinstance(data, dict) else {}


def _write_wrap_up_operation(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    state["updated_at"] = _now_iso()
    _write_json(wrap_up_operation_path(root, str(state["operation_id"])), state)
    return state


def begin_wrap_up_operation(
    root: Path,
    *,
    idempotency_key: str = "",
    project_folder: str = "",
    source_tool: str = "",
    budget_ms: int,
    closeout_mode: str,
) -> tuple[dict[str, Any], bool]:
    """Create or reuse a metadata-only wrap-up operation record.

    The record intentionally stores no session summary or local project path.
    A caller-provided idempotency key maps to a deterministic opaque operation
    id so a retry can learn whether an earlier call is still running or already
    finished without duplicating session writes.
    """
    key = (idempotency_key or "").strip()
    operation_id = new_wrap_up_operation_id(key)

    def _create_or_replay() -> tuple[dict[str, Any], bool]:
        existing = read_wrap_up_operation(root, operation_id) if key else {}
        if existing:
            existing["idempotent_replay"] = True
            return existing, True

        source: dict[str, Any] = {
            "source_tool": (source_tool or "")[:40],
            "has_project": bool(project_folder),
        }
        if project_folder:
            source["project_id"] = _project_id(project_folder)

        now = _now_iso()
        state: dict[str, Any] = {
            "schema": "wrap_up_operation.v1",
            "operation_id": operation_id,
            "status": "running",
            "current_stage": "accepted",
            "created_at": now,
            "updated_at": now,
            "source": source,
            "budget": {
                "budget_ms": budget_ms,
                "closeout_mode": closeout_mode,
            },
            "idempotency": {
                "key_provided": bool(key),
                "key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                if key
                else "",
            },
            "stages": {},
            "committed": {},
            "diagnostics": {
                "safe_retry": "same idempotency_key will not duplicate terminal operations",
            },
        }
        _write_wrap_up_operation(root, state)
        return state, False

    if not key:
        return _create_or_replay()

    operations_dir = wrap_up_operations_dir(root)
    operations_dir.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(
        operations_dir / ".engram-begin.lock",
        "a",
        timeout=5,
    ):
        return _create_or_replay()


def mark_wrap_up_stage(
    root: Path,
    operation_id: str,
    stage: str,
    status: str,
    *,
    timing_ms: int | None = None,
    reason: str = "",
    error: str = "",
    counts: dict[str, int] | None = None,
    committed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = read_wrap_up_operation(root, operation_id)
    if not state:
        return state
    stages = state.setdefault("stages", {})
    entry = dict(stages.get(stage) or {})
    entry["status"] = status
    entry["updated_at"] = _now_iso()
    if status == "running":
        entry.setdefault("started_at", entry["updated_at"])
        state["current_stage"] = stage
        state["status"] = "running"
    else:
        entry["finished_at"] = entry["updated_at"]
    if timing_ms is not None:
        entry["timing_ms"] = max(0, int(timing_ms))
    if reason:
        entry["reason"] = reason
    if error:
        entry["error"] = error
    if counts:
        entry["counts"] = {k: max(0, int(v)) for k, v in counts.items()}
    stages[stage] = entry
    if committed:
        state.setdefault("committed", {}).update(committed)
    return _write_wrap_up_operation(root, state)


def finish_wrap_up_operation(
    root: Path,
    operation_id: str,
    *,
    status: str,
    timing_ms: int,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"invalid terminal status: {status}")
    state = read_wrap_up_operation(root, operation_id)
    if not state:
        return state
    state["status"] = status
    state["current_stage"] = ""
    state["finished_at"] = _now_iso()
    state["total_ms"] = max(0, int(timing_ms))
    if outcome:
        state["outcome"] = outcome
    return _write_wrap_up_operation(root, state)


def public_wrap_up_operation_status(state: dict[str, Any]) -> dict[str, Any]:
    """Return operation metadata without raw summary text or local paths."""
    allowed = {
        "schema",
        "operation_id",
        "status",
        "current_stage",
        "created_at",
        "updated_at",
        "finished_at",
        "source",
        "budget",
        "idempotency",
        "stages",
        "committed",
        "diagnostics",
        "total_ms",
        "outcome",
        "idempotent_replay",
    }
    public = deepcopy({k: v for k, v in state.items() if k in allowed})
    if public.get("status") != "running":
        return public

    updated_at = str(public.get("updated_at") or "")
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_ms = max(
            0,
            int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() * 1000),
        )
    except (TypeError, ValueError):
        age_ms = 0

    budget = public.get("budget") if isinstance(public.get("budget"), dict) else {}
    try:
        budget_ms = max(1, int(budget.get("budget_ms") or DEFAULT_WRAP_UP_MAX_MS))
    except (TypeError, ValueError):
        budget_ms = DEFAULT_WRAP_UP_MAX_MS
    stale_after_ms = budget_ms + RUNNING_STALE_GRACE_MS

    diagnostics = public.setdefault("diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        public["diagnostics"] = diagnostics
    diagnostics["running_age_ms"] = age_ms
    diagnostics["stale_after_ms"] = stale_after_ms
    diagnostics["possibly_interrupted"] = age_ms >= stale_after_ms
    if age_ms >= stale_after_ms:
        public["persisted_status"] = "running"
        public["status"] = "stale_running"
        diagnostics["reason"] = "operation_exceeded_budget_without_terminal_update"
    return public
