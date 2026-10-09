"""Cursor stop / sessionEnd: queue a bounded summary or transcript reference. Capture debounce is local; the offline processor writes checkpoints without knowledge extraction."""

from __future__ import annotations

import os
import sys
from datetime import datetime

from . import _cursor_payload as payload
from ._log import log_failure

_ACTIVE_ENV = "ENGRAM_CURSOR_SAVE_ACTIVE"
_MAX_CONTENT_CHARS = 4000
_FINAL_EVENTS = {"sessionend", "session_end"}


def _debounce_minutes() -> int:
    raw = os.environ.get("ENGRAM_CURSOR_SAVE_DEBOUNCE", "10")
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return 10


def main() -> int:
    if os.environ.get(_ACTIVE_ENV) == "1":
        return 0
    os.environ[_ACTIVE_ENV] = "1"
    try:
        payload.apply_argv_env(sys.argv[1:])
        payload.reconfigure_stdin_utf8()
        from ._producer import cursor_event
        return cursor_event("cursor_save", payload.parse_event(sys.argv[1:]) or "stop",
                            debounce_minutes=_debounce_minutes())
    except Exception as exc:
        log_failure("cursor_save_on_stop", "hook failed (" + type(exc).__name__ + ")")
        return 0
    finally:
        os.environ.pop(_ACTIVE_ENV, None)

if __name__ == "__main__":
    raise SystemExit(main())
