"""A bounded daemon worker for synchronous, read-only SessionStart output.

The budget covers the whole worker read: importing ``piia_engram.core``, opening
the read-only handle and building the brief. Only the interpreter startup before
``read_with_budget`` is called lies outside it. A timed-out read never blocks
the host: the hook still answers ``{"continue": true}`` and exits 0, without the
brief, and records ``resume read budget exceeded`` in ``hooks.log``;
``engram doctor`` reports recent entries.

``READ_BUDGET_SECONDS`` is the default. ``ENGRAM_RESUME_HOOK_BUDGET_S`` (float
seconds) overrides it: an unset, blank or invalid value falls back to the
default, and a valid one is raised to ``MIN_BUDGET_SECONDS``. Every budget is
capped at ``HOOK_TIMEOUT_SECONDS`` for the hook minus ``HOST_MARGIN_SECONDS``,
so Engram gives up before the host would kill the hook.
"""
from __future__ import annotations

import math
import os
import threading
import time

from ._log import log_failure

#: Default seconds a resume read (import included) may take.
READ_BUDGET_SECONDS = 10.0
#: Environment override, in float seconds.
BUDGET_ENV = "ENGRAM_RESUME_HOOK_BUDGET_S"
#: Floor for the override: below it the read (import alone is about half a
#: second on a slow machine) can only ever time out.
MIN_BUDGET_SECONDS = 1.0
#: Time left for the hook to answer the host before the host timeout ends it.
HOST_MARGIN_SECONDS = 1.0
#: Host timeout each resume hook is registered with, in seconds. SessionStart is
#: registered with timeout 15 (``_inject_claude_code_sessionstart_hook``). Engram
#: registers no Cursor hook and Cursor documents only a "platform default" for a
#: hook's ``timeout``, so the same value keeps that budget conservative.
HOOK_TIMEOUT_SECONDS = {
    "auto_inject_resume_brief": 15.0,
    "cursor_inject_resume_brief": 15.0,
}


def budget_seconds(hook: str) -> float:
    """Seconds one run of ``hook`` may spend on its read, import included."""
    seconds = READ_BUDGET_SECONDS
    raw = os.environ.get(BUDGET_ENV, "").strip()
    if raw:
        try:
            override = float(raw)
        except ValueError:
            override = math.nan
        if math.isfinite(override):
            seconds = max(MIN_BUDGET_SECONDS, override)
    host_timeout = HOOK_TIMEOUT_SECONDS.get(hook)
    if host_timeout is not None:
        seconds = min(seconds, host_timeout - HOST_MARGIN_SECONDS)
    return seconds


def read_with_budget(reader, hook: str) -> str:
    budget = budget_seconds(hook)
    deadline = time.monotonic() + budget
    result = []
    errors = []
    finished = threading.Event()

    def work():
        try:
            # ContextVars do not propagate from the hook entry thread. Apply
            # the corruption policy here, around construction and all reads.
            from piia_engram.storage import non_quarantining_reads

            with non_quarantining_reads():
                result.append(reader())
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            finished.set()

    threading.Thread(target=work, name="engram-hook-read", daemon=True).start()
    diagnostic_reserve = min(0.025, budget / 4)
    if not finished.wait(max(0, deadline - time.monotonic() - diagnostic_reserve)):
        # Reserve time for one fail-soft write on the calling thread. A daemon
        # diagnostic can be killed before it writes when the hook exits.
        log_failure(hook, "resume read budget exceeded")
        return ""
    if errors:
        log_failure(hook, "resume read failed (" + errors[0] + ")")
    return result[0] if result else ""
