"""``os.replace`` for atomic writes, tolerant of short Windows sharing conflicts.

On Windows, replacing a file fails while another handle to the target is open
without FILE_SHARE_DELETE. Python's ``open()`` opens files that way, so any
reader of the target -- another Engram process or AI client reading the store
without the write lock, an antivirus or search indexer scan -- makes a
concurrent atomic write fail with WinError 5 (access denied) or 32 (sharing
violation) for the few milliseconds it holds the file.

:func:`replace_with_retry` retries exactly those errors, with backoff, for a
bounded time. Any other error, any other platform, or a conflict that outlasts
the budget re-raises the original exception, so a real permission problem is
still reported (after at most ``_RETRY_TIMEOUT`` seconds).

The retry sleeps in the calling thread: a blocked write holds that thread for
up to about one second before it succeeds or raises. A write made from an
async MCP tool handler therefore stalls the event loop for that long; it only
happens while another handle holds the target file.

A blocked-write warning names the file (base name only) and the Windows error
code, never the exception message, which carries the full path.

Standard library only: imported by modules that must stay light at startup.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"
# ERROR_ACCESS_DENIED (target open without share-delete, or pending delete)
# and ERROR_SHARING_VIOLATION.
_TRANSIENT_WINERRORS = frozenset({5, 32})
_RETRY_TIMEOUT = 1.0  # seconds, from the first failure
_RETRY_FIRST_DELAY = 0.005
_RETRY_MAX_DELAY = 0.1


def _is_transient(exc: OSError) -> bool:
    return (
        _IS_WINDOWS
        and isinstance(exc, PermissionError)
        and getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS
    )


def replace_with_retry(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """``os.replace(src, dst)``, retrying a transient Windows sharing conflict.

    Blocks the calling thread for up to about one second while the conflict lasts.
    """
    deadline: float | None = None
    delay = _RETRY_FIRST_DELAY
    attempts = 0
    while True:
        attempts += 1
        try:
            os.replace(src, dst)
        except PermissionError as exc:
            if not _is_transient(exc):
                raise
            now = time.monotonic()
            if deadline is None:
                deadline = now + _RETRY_TIMEOUT
            remaining = deadline - now
            if remaining <= 0:
                logger.warning(
                    "replace of %s still blocked after %d attempts (winerror %s)",
                    os.path.basename(os.fspath(dst)), attempts, exc.winerror,
                )
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _RETRY_MAX_DELAY)
            continue
        if attempts > 1:
            logger.debug(
                "replace of %s succeeded after %d attempts",
                os.path.basename(os.fspath(dst)), attempts,
            )
        return
