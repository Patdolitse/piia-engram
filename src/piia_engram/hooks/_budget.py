"""A bounded daemon worker for synchronous, read-only SessionStart output."""
from __future__ import annotations

import threading
import time

from ._log import log_failure

READ_BUDGET_SECONDS = 1.0


def read_with_budget(reader, hook: str) -> str:
    deadline = time.monotonic() + READ_BUDGET_SECONDS
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
    diagnostic_reserve = min(0.025, READ_BUDGET_SECONDS / 4)
    if not finished.wait(max(0, deadline - time.monotonic() - diagnostic_reserve)):
        # Reserve time for one fail-soft write on the calling thread. A daemon
        # diagnostic can be killed before it writes when the hook exits.
        log_failure(hook, "resume read budget exceeded")
        return ""
    if errors:
        log_failure(hook, "resume read failed (" + errors[0] + ")")
    return result[0] if result else ""
