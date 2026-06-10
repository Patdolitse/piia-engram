"""Universal watcher core: poll adapters, watermark changes, save checkpoints.

One scan (:func:`scan_once`):

1. each adapter discovers recent transcript files (read-only);
2. files whose ``(mtime, size)`` watermark is unchanged are skipped;
3. changed files are parsed into a summary and appended to the Engram
   ``contexts/`` session log via ``save_agent_context`` (never the knowledge
   store), debounced per session;
4. *optionally* (off by default, ``ENGRAM_WATCHER_WRITEBACK=1``) the same
   summary is distilled into knowledge **proposals** via
   ``extract_session_insights(force_staging=True)`` — every item lands in
   staging for explicit owner review, never directly in verified knowledge.
   This mirrors the Cursor writeback precedent (``ENGRAM_CURSOR_WRITEBACK``)
   and keeps the "watcher writes contexts, not knowledge" boundary intact
   unless the owner explicitly opts in;
5. the watermark state is persisted to ``<ENGRAM_DIR>/logs/watcher_state.json``.

First-run baseline: when a file is already present *before* the watcher has
any state for it and its mtime predates the first scan, it is baselined
without saving — the watcher captures new activity, it does not backfill
months of historical transcripts into contexts/.

Debounce: an active conversation keeps touching its transcript, so loop mode
would otherwise save every interval. Saves for the same session are limited
to one per ``ENGRAM_WATCHER_DEBOUNCE`` minutes (default 10; ``0`` disables).
Unlike the Cursor hook there is no "final" event to bypass the debounce; the
recovery granularity is therefore <= the debounce window, with the *next*
scan after the window picking up whatever the last save missed (watermarks
only advance on successful saves).

Fail-soft by contract: a broken adapter or store error must never kill the
loop; errors are appended to ``<ENGRAM_DIR>/logs/watcher.log``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from . import codex_adapter

_STATE_FILE = "watcher_state.json"
_LOG_FILE = "watcher.log"
_MAX_CONTENT_CHARS = 4000
_DEBOUNCE_ENV = "ENGRAM_WATCHER_DEBOUNCE"
_SINCE_DAYS_ENV = "ENGRAM_WATCHER_SINCE_DAYS"
#: Opt-in gate for staging-only knowledge distillation (off by default).
WRITEBACK_ENV = "ENGRAM_WATCHER_WRITEBACK"

#: Adapter registry: name -> (discover, parse). New tools plug in here.
ADAPTERS: dict[str, dict[str, Callable[..., Any]]] = {
    codex_adapter.TOOL_NAME: {
        "discover": codex_adapter.discover,
        "parse": codex_adapter.parse,
    },
}


def _state_dir() -> Path:
    base = os.environ.get("ENGRAM_DIR", "").strip() or "~/.engram"
    return Path(base).expanduser() / "logs"


def _log(message: str) -> None:
    """Append one line to the watcher log. Never raises."""
    try:
        directory = _state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(directory / _LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def _load_state() -> dict[str, Any]:
    try:
        raw = (_state_dir() / _STATE_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        directory = _state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=True, indent=0), encoding="utf-8"
        )
    except Exception:
        pass


def _debounce_minutes() -> int:
    raw = os.environ.get(_DEBOUNCE_ENV, "10")
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return 10


def since_days() -> int:
    raw = os.environ.get(_SINCE_DAYS_ENV, "3")
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return 3


def _recently_saved(entry: dict[str, Any], window_minutes: int) -> bool:
    """True when this file's last save falls inside the debounce window.

    Errors degrade toward "save again" — an extra context append is cheap,
    a dropped checkpoint is not.
    """
    if window_minutes <= 0:
        return False
    last = entry.get("saved_at")
    if not isinstance(last, str) or not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    return datetime.now() - last_ts < timedelta(minutes=window_minutes)


def _fingerprint(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return None


def writeback_enabled() -> bool:
    """True when the owner has explicitly opted in to staging-only writeback."""
    try:
        from piia_engram.hooks.writeback_policy import check_writeback_allowed

        return check_writeback_allowed(WRITEBACK_ENV, staging_gate=True)
    except Exception:
        return False


def _maybe_writeback(tool: str, summary: str) -> int:
    """Distill a saved checkpoint into staging knowledge proposals (opt-in).

    Off by default. When ``ENGRAM_WATCHER_WRITEBACK=1`` every extracted item
    is forced into staging for explicit owner review (``force_staging=True``)
    — the watcher never writes verified knowledge directly. Failures are
    logged and swallowed: distillation must never break the capture loop or
    block the watermark advance (the checkpoint save already succeeded).

    Returns the number of items staged (0 when disabled or on error).
    """
    try:
        if not writeback_enabled():
            return 0
        from piia_engram.core import Engram

        result = Engram().extract_session_insights(
            summary, source_tool=tool, force_staging=True
        )
        staged = int(result.get("saved_lessons", 0) or 0) + int(
            result.get("saved_decisions", 0) or 0
        )
        if staged:
            _log(
                f"{tool}: writeback staged {staged} item(s) for review "
                f"(duplicates={result.get('duplicates', 0)}, "
                f"skipped={result.get('skipped', 0)})"
            )
        return staged
    except Exception as exc:  # noqa: BLE001 - fail-soft loop contract
        _log(f"{tool}: writeback failed (non-fatal): {exc!r}")
        return 0


def scan_once(
    adapters: Iterable[str] | None = None,
    *,
    baseline_existing: bool | None = None,
) -> dict[str, int]:
    """Run one watch cycle. Returns counters for observability/tests.

    ``baseline_existing``: when ``None`` (default), files unseen by the state
    are baselined without saving *only if* this tool has never been scanned
    before (true first run). Pass ``False`` to force-save unseen files (used
    by tests and by an explicit one-shot capture).
    """
    counters = {
        "discovered": 0,
        "saved": 0,
        "skipped": 0,
        "baselined": 0,
        "errors": 0,
        "writeback_items": 0,
    }
    state = _load_state()
    debounce = _debounce_minutes()
    names = list(adapters) if adapters is not None else list(ADAPTERS)

    for name in names:
        adapter = ADAPTERS.get(name)
        if adapter is None:
            _log(f"unknown adapter: {name}")
            counters["errors"] += 1
            continue

        tool_state = state.get(name)
        first_run = not isinstance(tool_state, dict)
        if first_run:
            tool_state = {}
            state[name] = tool_state
        baseline = first_run if baseline_existing is None else baseline_existing

        try:
            paths = list(adapter["discover"](since_days()))
        except Exception as exc:  # noqa: BLE001 - fail-soft loop contract
            _log(f"{name}: discover failed: {exc!r}")
            counters["errors"] += 1
            continue

        for path in paths:
            counters["discovered"] += 1
            fp = _fingerprint(path)
            if fp is None:
                counters["errors"] += 1
                continue
            key = str(path)
            entry = tool_state.get(key)
            if not isinstance(entry, dict):
                entry = {}
            known = (entry.get("mtime"), entry.get("size"))
            if known == (fp[0], fp[1]):
                counters["skipped"] += 1
                continue
            if baseline and not entry:
                tool_state[key] = {"mtime": fp[0], "size": fp[1]}
                counters["baselined"] += 1
                continue
            if _recently_saved(entry, debounce):
                counters["skipped"] += 1
                continue

            try:
                parsed = adapter["parse"](path, _MAX_CONTENT_CHARS)
            except Exception as exc:  # noqa: BLE001
                _log(f"{name}: parse failed for {path.name}: {exc!r}")
                counters["errors"] += 1
                continue

            summary = parsed.get("summary", "")
            if not summary.strip():
                # Transcript exists but carries no conversation yet (e.g.
                # only meta/system lines). Advance the watermark so it is
                # re-checked only after it grows.
                tool_state[key] = {"mtime": fp[0], "size": fp[1]}
                counters["skipped"] += 1
                continue

            content = f"[{name.capitalize()} Watcher 自动记录]\n"
            project_folder = parsed.get("project_folder", "")
            if project_folder:
                content += f"工作目录: {project_folder}\n"
            content += "---\n" + summary

            try:
                from piia_engram.core import Engram

                Engram().save_agent_context(
                    tool=name,
                    content=content,
                    session_id=parsed.get("session_id") or path.stem,
                    project_folder=project_folder,
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"{name}: save failed for {path.name}: {exc!r}")
                counters["errors"] += 1
                continue  # watermark NOT advanced -> retried next scan

            tool_state[key] = {
                "mtime": fp[0],
                "size": fp[1],
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            }
            counters["saved"] += 1
            # Optional staging-only distillation — after the checkpoint save
            # succeeded and the watermark advanced. Never affects the loop.
            counters["writeback_items"] += _maybe_writeback(name, summary)

        # Prune state entries for files outside the discovery window so the
        # state file does not grow unboundedly across months of sessions.
        live = {str(p) for p in paths}
        for key in list(tool_state):
            if key not in live:
                del tool_state[key]

    _save_state(state)
    return counters
