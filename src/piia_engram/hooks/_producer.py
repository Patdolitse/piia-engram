"""Minimal lifecycle payloads: no transcript reads or store initialization."""
from __future__ import annotations

import os
import json
import sys

from . import _cursor_payload as payload
from ._log import log_failure
from .spool import enqueue


def _read_input() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook payload must be an object")
    return value


def claude_event(kind: str, threshold: int = 10) -> int:
    try:
        hook_input = _read_input()
        transcript = str(hook_input.get("transcript_path") or "")
        if not transcript:
            return 0
        enqueue(kind, "claude_code", {"transcript_path": transcript,
                                     "project_folder": str(hook_input.get("cwd") or ""),
                                     "threshold": threshold})
    except Exception as exc:
        log_failure(kind, "capture failed (" + type(exc).__name__ + ")")
    return 0


def cursor_event(kind: str, event: str = "stop", *, debounce_minutes: int = 0) -> int:
    try:
        hook_input = _read_input()
        project = payload.extract_project_folder(hook_input)
        session = payload.extract_session_id(hook_input)
        if (kind == "cursor_save" and event.lower() not in {"sessionend", "session_end"}
                and payload.recently_saved(session or "_default", debounce_minutes)):
            return 0
        maximum = 4000 if kind == "cursor_save" else 20_000
        text = ""
        for key in payload._SUMMARY_KEYS:
            candidate = payload.coerce_text(hook_input.get(key))
            if candidate.strip():
                text = candidate.strip()[-maximum:]
                break
        transcript = str(hook_input.get("transcript_path") or "")
        if kind == "cursor_save":
            transcript = transcript or os.environ.get("CURSOR_TRANSCRIPT_PATH", "")
        if kind == "cursor_writeback" and not text and not transcript:
            return 0
        roots = [str(p) for p in payload._transcript_allowlisted_roots(hook_input)]
        event_id = enqueue(kind, "cursor", {"summary": text, "transcript_path": transcript,
                                 "project_folder": project, "session_id": session,
                                 "event": event, "roots": roots,
                                 "hook_cwd": os.getcwd() if kind == "cursor_save" else ""})
        if event_id and kind == "cursor_save":
            payload.mark_saved(session or "_default")
    except Exception as exc:
        log_failure(kind, "capture failed (" + type(exc).__name__ + ")")
    return 0
