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

Real Cursor protocol (probed 2026-06-10): stdin payloads arrive *empty*;
session data travels via environment variables instead. The shared extractors
therefore fall back to ``CURSOR_TRANSCRIPT_PATH`` (conversation transcript →
summary text + session id from the file stem) and ``CURSOR_PROJECT_DIR``
(workspace folder). Only when even those yield nothing does the hook degrade
to a *minimal checkpoint* (event + hook cwd) appended to a shared per-day
``hook-YYYY-MM-DD`` session file — it never silently saves nothing.

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
        # Probe v1 evidence (2026-06-10): real Cursor sends an *empty* stdin
        # payload on stop/sessionEnd, so an extract-or-skip policy would mean
        # the save path never fires. Degrade instead: write a minimal
        # checkpoint ("a Cursor session was active here at this time") so the
        # cross-tool activity trail survives until the protocol probe finds a
        # richer source. Rich extraction resumes automatically if Cursor ever
        # does provide content fields.
        degraded = not summary

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
        if degraded:
            try:
                hook_cwd = os.getcwd()
            except OSError:
                hook_cwd = ""
            content += "（Cursor 本次事件未携带会话内容 payload，记录最小检查点。）\n"
            if hook_cwd:
                content += f"hook 进程 cwd: {hook_cwd}\n"
        else:
            content += summary

        if session_id:
            save_session_id = session_id
        elif degraded:
            # One shared per-day file for minimal checkpoints: each save
            # appends a timestamped entry instead of spawning a new context
            # file every debounce window.
            save_session_id = f"hook-{datetime.now().strftime('%Y-%m-%d')}"
        else:
            save_session_id = f"hook-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"

        try:
            from piia_engram.core import Engram

            Engram().save_agent_context(
                tool="cursor",
                content=content,
                session_id=save_session_id,
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
