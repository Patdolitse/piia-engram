"""Portable file ids and resolved containment for store-owned files."""

from __future__ import annotations

import re
from pathlib import Path

_ID_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,158}[A-Za-z0-9_-])?")
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(10)
})


def valid_file_id(value: object) -> bool:
    return (isinstance(value, str) and _ID_RE.fullmatch(value) is not None
            and ".." not in value and value.split(".")[0].upper() not in _DEVICES)


def confined_path(base: Path, *parts: str) -> Path:
    """Return a lexical path only if its resolved target stays below base."""
    path = base.joinpath(*parts)
    resolved_base = base.resolve()
    resolved = path.resolve()
    if resolved == resolved_base or not resolved.is_relative_to(resolved_base):
        raise ValueError("path is outside its store directory")
    return path
