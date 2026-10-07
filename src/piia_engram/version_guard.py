"""Required ``expected_version`` for MCP writes that change an existing entry.

Over MCP, every call that modifies, archives, merges, deletes or proposes to
supersede an existing lesson, decision or playbook must say which version of
that entry it is based on. The version is in every read result (``version``;
an entry that was never revised is version 1).

* missing  -> ``{"error": "version_required", "current_version": N, "example": {...}}``
* stale    -> ``{"error": "version_conflict", "current_version": N}``

Both write nothing. New entries, purely additive writes and reads need no
version. The Owner's local commands keep their own version checks and are not
affected.
"""

from __future__ import annotations

from typing import Any, Mapping

ERROR_REQUIRED = "version_required"
ERROR_CONFLICT = "version_conflict"


def current_version(row: Mapping[str, Any] | None) -> int:
    try:
        return int((row or {}).get("version") or 1)
    except (TypeError, ValueError):
        return 1


def parse(value: Any) -> int | None:
    """A caller's expected version: an int, None when absent, -1 when malformed (never matches)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def required(item_id: str, version: int, example: Mapping[str, Any], *, param: str = "expected_version") -> dict:
    return {
        "error": ERROR_REQUIRED,
        "item_id": item_id,
        "param": param,
        "current_version": int(version),
        "changed": False,
        "message": (
            f"Changing an existing entry over MCP requires {param} (the version from a read result). "
            "Nothing was written; retry with the version shown here after checking the entry."
        ),
        "example": dict(example),
    }


def conflict(item_id: str, expected: int, version: int, *, param: str = "expected_version") -> dict:
    return {
        "error": ERROR_CONFLICT,
        "item_id": item_id,
        "param": param,
        "expected_version": expected,
        "current_version": int(version),
        "actual_version": int(version),
        "changed": False,
        "message": "The entry changed since it was read. Nothing was written; read it again and retry.",
    }


def check(item_id: str, row: Mapping[str, Any], expected: Any, example: Mapping[str, Any],
          *, param: str = "expected_version") -> dict | None:
    """The refusal for ``expected`` against ``row``, or None when it matches."""
    version = current_version(row)
    wanted = parse(expected)
    if wanted is None:
        filled = {key: (version if value is None else value) for key, value in example.items()}
        return required(item_id, version, filled, param=param)
    if wanted != version:
        return conflict(item_id, wanted, version, param=param)
    return None


def with_version(item: Any) -> Any:
    """A copy of a read-result row that carries ``version`` (1 when never revised)."""
    if not isinstance(item, dict) or not item.get("id") or "version" in item:
        return item
    if item.get("governance_withheld"):
        return item
    out = dict(item)
    out["version"] = 1
    return out


def with_versions(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    return [with_version(item) for item in items]
