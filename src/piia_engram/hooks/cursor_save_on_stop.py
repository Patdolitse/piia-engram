"""Cursor stop / sessionEnd hook: save a lightweight session checkpoint.

Cursor-side twin of ``auto_save_on_stop`` (Claude Code), with one deliberate
narrowing: it only ever calls ``save_agent_context`` (the ``contexts/``
append-only session log). It never calls ``wrap_up_session`` and never writes
to the knowledge store — knowledge extraction from Cursor sessions stays
behind the separate, off-by-default ``cursor_writeback`` hook and its
``ENGRAM_CURSOR_WRITEBACK`` staging gate.

Debounce: Cursor's ``stop`` event fires at the end of *every* agent loop, so
un-throttled saves would write one context file per turn. Saves for the same
session are therefore debounced to one per ``ENGRAM_CURSOR_SAVE_DEBOUNCE``
minutes (default 10; ``0`` disables debouncing). A ``--event sessionEnd``
invocation bypasses the debounce — the final save of a session must never be
dropped. This yields force-kill recovery granularity of <= the debounce
window.

Invoked as ``python -m piia_engram.hooks.cursor_save_on_stop [--event stop]``.
Fail-silent by contract: always exits 0; a broken hook must never block Cursor.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from . import _cursor_payload as payload

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
    payload.apply_argv_env(sys.argv[1:])
    event = payload.parse_event(sys.argv[1:]) or "stop"
    payload.reconfigure_stdin_utf8()

    if os.environ.get(_ACTIVE_ENV) == "1":
        return 0
    os.environ[_ACTIVE_ENV] = "1"

    try:
        hook_input = payload.read_hook_input()
        payload.debug_log(event, hook_input)

        summary = payload.extract_summary(hook_input, _MAX_CONTENT_CHARS)
        if not summary:
            return 0

        project_folder = payload.extract_project_folder(hook_input)
        session_id = payload.extract_session_id(hook_input)
        debounce_key = session_id or "_default"

        is_final = event.strip().lower() in _FINAL_EVENTS
        if not is_final and payload.recently_saved(debounce_key, _debounce_minutes()):
            return 0

        content = f"[Cursor Hook 自动记录 · {event}]\n"
        if project_folder:
            content += f"工作目录: {project_folder}\n"
        content += "---\n"
        content += summary

        try:
            from piia_engram.core import Engram

            Engram().save_agent_context(
                tool="cursor",
                content=content,
                session_id=session_id
                or f"hook-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}",
                project_folder=project_folder,
            )
        except Exception:
            return 0

        payload.mark_saved(debounce_key)
        return 0
    except Exception:
        return 0
    finally:
        os.environ.pop(_ACTIVE_ENV, None)


if __name__ == "__main__":
    raise SystemExit(main())
