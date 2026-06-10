"""Shared failure logging for Engram hooks.

Hooks must never block the host tool (Claude Code / Cursor), so every
entry point swallows exceptions. Before this module they swallowed them
*silently* — a failed save was invisible to the user. ``log_failure``
gives each swallow site a one-line breadcrumb in
``<ENGRAM_DIR>/logs/hooks.log`` (same directory as ``watcher.log``) so
failures stay diagnosable without ever blocking the host.
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path

_LOG_FILE = "hooks.log"
#: Cap so a permanently-broken install can't grow the log unbounded
#: (hooks fire every session). On overflow the file is reset, keeping
#: the most recent failures.
_MAX_LOG_BYTES = 1_000_000


def _log_dir() -> Path:
    base = os.environ.get("ENGRAM_DIR", "").strip() or "~/.engram"
    return Path(base).expanduser() / "logs"


def log_failure(hook: str, message: str, exc: BaseException | None = None) -> None:
    """Append one failure line to hooks.log. Never raises."""
    try:
        directory = _log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _LOG_FILE
        try:
            if path.stat().st_size > _MAX_LOG_BYTES:
                path.unlink()
        except OSError:
            pass
        detail = message
        if exc is not None:
            last = traceback.format_exception_only(type(exc), exc)[-1].strip()
            detail = f"{message}: {last}"
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} [{hook}] {detail}\n")
    except Exception:
        pass
