"""A bounded daemon worker for synchronous, read-only SessionStart output."""
from __future__ import annotations

import threading
import time

from ._log import log_failure

READ_BUDGET_SECONDS = 1.0


def read_with_budget(reader, hook: str) -> str:
    deadline = time.monotonic() + READ_BUDGET_SECONDS
    result = []
    finished = threading.Event()

    def work():
        try:
            result.append(reader())
        except Exception as exc:
            log_failure(hook, "resume read failed (" + type(exc).__name__ + ")")
        finally:
            finished.set()

    threading.Thread(target=work, name="engram-hook-read", daemon=True).start()
    diagnostic_reserve = min(0.025, READ_BUDGET_SECONDS / 4)
    if not finished.wait(max(0, deadline - time.monotonic() - diagnostic_reserve)):
        # Logging must not extend the output deadline on an inaccessible disk.
        diagnostic = threading.Thread(target=log_failure, args=(hook, "resume read budget exceeded"),
                                      daemon=True)
        diagnostic.start()
        diagnostic.join(max(0, deadline - time.monotonic()))
        return ""
    return result[0] if result else ""
