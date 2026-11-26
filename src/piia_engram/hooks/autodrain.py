"""Process the hook queue without anyone running ``engram hooks drain``.

Hooks only queue events (``spool.enqueue``). This module runs the same
``spool.drain`` from two places that are already running on the owner's
machine: a background thread of the MCP server and the watcher's scan loop.

What a pass may do is what the drain command may do, a few events at a time:
session records, digests and review-queue proposals. It never runs as an AI
client's write (``spool.drain`` leaves the MCP origin) and nothing it writes
is approved knowledge. ``ENGRAM_SPOOL_AUTODRAIN=0`` turns it off.

Never imported by hook producers.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ._log import log_failure
from . import spool

#: ``0`` / ``false`` / ``off`` / ``no`` turns automatic processing off.
ENV_SWITCH = "ENGRAM_SPOOL_AUTODRAIN"
#: Seconds the MCP server waits after its start before the first pass.
ENV_DELAY = "ENGRAM_SPOOL_AUTODRAIN_DELAY_S"

DEFAULT_DELAY_SECONDS = 15.0
MAX_DELAY_SECONDS = 3600.0
INTERVAL_SECONDS = 600.0
CATCH_UP_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 7200.0
#: One pass: at most this many events and this long, so a large backlog is
#: worked off over several passes instead of holding the store.
PASS_MAX_EVENTS = 50
PASS_DEADLINE_SECONDS = 20.0
WATCHER_MIN_GAP_SECONDS = 60.0

_OFF = {"0", "false", "off", "no"}
_STATUS_KEYS = ("processed", "duplicates", "failed", "quarantined", "pending", "busy", "limited")
_last_watcher_pass: float | None = None


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() not in _OFF


def first_delay() -> float:
    """Seconds before the server's first pass; a bad value means the default."""
    raw = os.environ.get(ENV_DELAY, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DELAY_SECONDS
    if not math.isfinite(value) or value < 0:
        return DEFAULT_DELAY_SECONDS
    return min(value, MAX_DELAY_SECONDS)


def status_path(root: Path | None = None) -> Path:
    """Where the last automatic pass is recorded (counts and a time, no content)."""
    return spool.store_root(root) / "hooks" / "spool-autodrain.json"


def read_status(root: Path | None = None) -> dict | None:
    """The last automatic pass, or None. Read-only; a damaged file is None."""
    try:
        path = status_path(root)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("ran_at"), str):
        return None
    status = {"schema": 1, "source": str(data.get("source") or "")[:32], "ran_at": data["ran_at"][:40]}
    for key in _STATUS_KEYS:
        value = data.get(key)
        status[key] = bool(value) if key in ("busy", "limited") else (
            value if type(value) is int and value >= 0 else 0)
    return status


def _write_status(root: Path, source: str, report: dict) -> None:
    status: dict[str, Any] = {"schema": 1, "source": source,
                              "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for key in _STATUS_KEYS:
        value = report.get(key, 0)
        status[key] = bool(value) if key in ("busy", "limited") else int(value or 0)
    path = status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    spool._publish(path, spool._json_bytes(status))


def run_pass(root: Path | None = None, *, source: str, engram=None,
             event_guard: Callable[[], Any] | None = None,
             should_stop: Callable[[], bool] | None = None) -> dict | None:
    """One bounded pass over the queue. None when off, empty or failed outright.

    Never raises. Without ``engram`` a store handle is opened only when there
    is something to process.
    """
    try:
        if not enabled():
            return None
        root = spool.store_root(root)
        if not spool.backlog(root)["pending"]:
            return None
        if engram is None:
            from ..core import Engram

            engram = Engram(root=root)
        report = spool.drain(root, engram=engram, max_events=PASS_MAX_EVENTS,
                             deadline_seconds=PASS_DEADLINE_SECONDS,
                             event_guard=event_guard, should_stop=should_stop)
        if report.get("busy"):
            return report  # another process is at it; its pass is the one to record
        try:
            _write_status(root, source, report)
        except Exception as exc:
            log_failure("hook_spool", "automatic pass status not written (" + type(exc).__name__ + ")",
                        root=root)
        if report.get("processed") or report.get("failed") or report.get("quarantined"):
            log_failure(
                "hook_spool",
                f"automatic pass ({source}): {report.get('processed', 0)} processed, "
                f"{report.get('duplicates', 0)} replays, {report.get('quarantined', 0)} quarantined, "
                f"{report.get('failed', 0)} failed, {report.get('pending', 0)} pending",
                root=root)
        return report
    except Exception as exc:
        try:
            log_failure("hook_spool", "automatic pass failed (" + type(exc).__name__ + ")",
                        root=spool.store_root(root))
        except Exception:
            pass
        return None


def next_wait(report: dict | None, previous: float) -> float | None:
    """Seconds until the next pass, or None to stop for this process.

    Leftover events come soon; a pass in which nothing got through backs off
    (doubling, at most two hours); a queue whose key is not available here is
    left to a process that has it.
    """
    if not report:
        return INTERVAL_SECONDS
    if report.get("protection_unavailable"):
        return None
    progressed = (report.get("processed") or report.get("duplicates") or report.get("quarantined"))
    if report.get("failed") and not progressed:
        return min(max(previous, INTERVAL_SECONDS) * 2, MAX_BACKOFF_SECONDS)
    if report.get("limited") and progressed:
        return CATCH_UP_SECONDS
    return INTERVAL_SECONDS


class AutoDrainer:
    """The MCP server's queue thread: a first delay, then a pass every interval."""

    def __init__(self, root: Path, *, engram=None, delay: float | None = None,
                 source: str = "mcp-server",
                 event_guard: Callable[[], Any] | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 on_pass: Callable[[dict | None], None] | None = None):
        self.root = Path(root)
        self.engram = engram
        self.delay = first_delay() if delay is None else float(delay)
        self.source = source
        self.event_guard = event_guard
        self._external_stop = should_stop
        self.on_pass = on_pass
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def should_stop(self) -> bool:
        if self._stop.is_set():
            return True
        try:
            return bool(self._external_stop and self._external_stop())
        except Exception:
            return False

    def start(self) -> "AutoDrainer":
        self.thread = threading.Thread(target=self._run, name="engram-spool-drain", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _run(self) -> None:
        wait: float | None = self.delay
        previous = INTERVAL_SECONDS
        while wait is not None:
            if self._stop.wait(wait) or self.should_stop():
                return
            report = run_pass(self.root, source=self.source, engram=self.engram,
                              event_guard=self.event_guard, should_stop=self.should_stop)
            if self.on_pass is not None:
                try:
                    self.on_pass(report)
                except Exception:
                    pass
            wait = next_wait(report, previous)
            if wait is not None:
                previous = wait


def watcher_pass(now: Callable[[], float] = time.monotonic) -> dict | None:
    """The watcher's pass after a scan: at most one a minute in this process."""
    global _last_watcher_pass
    if not enabled():
        return None
    moment = now()
    if _last_watcher_pass is not None and moment - _last_watcher_pass < WATCHER_MIN_GAP_SECONDS:
        return None
    _last_watcher_pass = moment
    return run_pass(None, source="watcher")
