"""Cursor end-of-session hook: optional staging-only Engram writeback.

This hook is intentionally off by default. It writes only when
``ENGRAM_CURSOR_WRITEBACK=1`` (or true/on/yes) is present, and then delegates to
``extract_session_insights`` so every captured item lands in staging for review.
"""

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
    if not _enabled():
        return 0
    if os.environ.get("ENGRAM_CURSOR_WRITEBACK_ACTIVE") == "1":
        return 0
    os.environ["ENGRAM_CURSOR_WRITEBACK_ACTIVE"] = "1"

    try:
        try:
            raw = sys.stdin.read()
            hook_input = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            return 0
        if not isinstance(hook_input, dict):
            return 0

        summary = _extract_summary(hook_input)
        if not summary:
            return 0

        try:
            from piia_engram.core import Engram

            result = Engram().extract_session_insights(
                summary, source_tool="cursor", force_staging=True
            )
        except Exception as exc:
            log_failure("cursor_writeback", "extract_session_insights failed", exc)
            return 0

        if os.environ.get("ENGRAM_CURSOR_WRITEBACK_DEBUG", "").strip().lower() in _TRUTHY:
            safe = {
                "saved_lessons": result.get("saved_lessons", 0),
                "saved_decisions": result.get("saved_decisions", 0),
                "duplicates": result.get("duplicates", 0),
                "skipped": result.get("skipped", 0),
                "tier": "staging",
            }
            print(json.dumps(safe, ensure_ascii=False))
        return 0
    finally:
        os.environ.pop("ENGRAM_CURSOR_WRITEBACK_ACTIVE", None)


if __name__ == "__main__":
    raise SystemExit(main())
