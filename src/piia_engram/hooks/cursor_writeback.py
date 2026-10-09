"""Opt-in Cursor sessionEnd: queue bounded input for deferred staging-only extraction. No memory-store operations run in the client lifecycle."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from ._log import log_failure
from .writeback_policy import check_writeback_allowed

_TRUTHY = {"1", "true", "on", "yes"}
_MAX_TEXT_CHARS = 20_000
_MAX_TRANSCRIPT_BYTES = 512_000


def _enabled() -> bool:
    return check_writeback_allowed("ENGRAM_CURSOR_WRITEBACK", staging_gate=True)


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(_coerce_text(item.get("text") or item.get("content") or ""))
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        return _coerce_text(value.get("text") or value.get("content") or "")
    return ""


def _summary_from_transcript(path: str, hook_input: dict | None = None) -> str:
    # v4.20: route through the shared hardened containment reader — this hook
    # fed its summary straight into staging extraction, so an uncontained read
    # here was the same read-oracle as the save hook.
    from ._cursor_payload import _summary_from_transcript as _hardened

    return _hardened(path, _MAX_TEXT_CHARS, hook_input=hook_input or {})


def _extract_summary(hook_input: dict) -> str:
    for key in ("summary", "session_summary", "text", "content"):
        text = _coerce_text(hook_input.get(key))
        if text.strip():
            return text.strip()[-_MAX_TEXT_CHARS:]
    return _summary_from_transcript(
        str(hook_input.get("transcript_path") or ""), hook_input=hook_input
    )


def main() -> int:
    active = False
    try:
        from . import _cursor_payload
        _cursor_payload.apply_argv_env(sys.argv[1:])
        _cursor_payload.reconfigure_stdin_utf8()
        if not _enabled() or os.environ.get("ENGRAM_CURSOR_WRITEBACK_ACTIVE") == "1":
            return 0
        os.environ["ENGRAM_CURSOR_WRITEBACK_ACTIVE"] = "1"
        active = True
        from ._producer import cursor_event
        return cursor_event("cursor_writeback", "sessionEnd")
    except Exception as exc:
        log_failure("cursor_writeback", "hook failed (" + type(exc).__name__ + ")")
        return 0
    finally:
        if active:
            os.environ.pop("ENGRAM_CURSOR_WRITEBACK_ACTIVE", None)

if __name__ == "__main__":
    raise SystemExit(main())
