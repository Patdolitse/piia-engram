"""Shared helpers for Cursor session hooks (inject / save).

Cursor's hook payloads are only partially documented, so these helpers are
deliberately defensive: every extractor tries several plausible field names,
every filesystem touch is fail-silent, and an opt-in debug probe
(``ENGRAM_HOOK_DEBUG=1``) records the *shape* of incoming payloads (top-level
keys plus short value previews) to ``<ENGRAM_DIR>/logs/`` so the field mapping
can be confirmed against one real session and tightened afterwards.

Nothing in this module writes to the knowledge store; the only writes are the
debug log and the save-debounce state file, both under ``<ENGRAM_DIR>/logs/``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_TRUTHY = {"1", "true", "on", "yes"}
_MAX_TRANSCRIPT_BYTES = 512_000
_PREVIEW_CHARS = 200
_STATE_RETENTION_HOURS = 24

#: Candidate payload keys for the workspace / project folder.
_PROJECT_KEYS = (
    "cwd",
    "workspace_path",
    "workspace_root",
    "workspace",
    "project_dir",
    "rootPath",
    "root_path",
)

#: Candidate payload keys for the session / conversation identifier.
_SESSION_KEYS = (
    "session_id",
    "conversation_id",
    "chat_id",
    "sessionId",
    "conversationId",
    "chatId",
)

#: Candidate payload keys carrying summary-ish text, in priority order.
_SUMMARY_KEYS = ("summary", "session_summary", "text", "content")


def apply_argv_env(argv: list[str]) -> None:
    """Promote ``--env KEY=VAL`` argv pairs into ``os.environ``.

    Windows shells don't accept the POSIX ``KEY=VAL prog`` inline prefix, so
    hook configs transport env hints through argv instead (same convention as
    ``auto_save_on_stop`` / ``auto_inject_resume_brief``).
    """
    i = 0
    while i < len(argv):
        if argv[i] == "--env" and i + 1 < len(argv):
            pair = argv[i + 1]
            if "=" in pair:
                key, _, value = pair.partition("=")
                key = key.strip()
                if key:
                    os.environ.setdefault(key, value)
            i += 2
            continue
        i += 1


def parse_event(argv: list[str]) -> str:
    """Return the value following ``--event``, or ``""`` when absent."""
    i = 0
    while i < len(argv):
        if argv[i] == "--event" and i + 1 < len(argv):
            return argv[i + 1].strip()
        i += 1
    return ""


def reconfigure_stdin_utf8() -> None:
    """Best-effort: force UTF-8 decoding of stdin on Windows pipes.

    Cursor writes UTF-8 JSON to the hook's stdin; without this, a Windows
    console codepage (e.g. cp936) would mangle Chinese summaries. Fail-silent:
    test harnesses replace stdin with objects lacking ``reconfigure``.
    """
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        pass


def read_hook_input() -> dict[str, Any]:
    """Read and parse the JSON payload from stdin; ``{}`` on any failure."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def coerce_text(value: Any) -> str:
    """Flatten str / list / dict payload shapes into plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(coerce_text(item.get("text") or item.get("content") or ""))
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        return coerce_text(value.get("text") or value.get("content") or "")
    return ""


def _summary_from_transcript(path: str, max_chars: int) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > _MAX_TRANSCRIPT_BYTES:
            return ""
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            lines.append(stripped)
            continue
        if not isinstance(entry, dict):
            continue
        text = coerce_text(
            entry.get("summary")
            or entry.get("text")
            or entry.get("content")
            or entry.get("message")
            or ""
        )
        if text:
            lines.append(text)
    return "\n".join(lines)[-max_chars:]


def extract_summary(hook_input: dict[str, Any], max_chars: int) -> str:
    """Summary text by field priority; transcript file as last resort.

    Keeps the *tail* when truncating — session conclusions beat openings.
    """
    for key in _SUMMARY_KEYS:
        text = coerce_text(hook_input.get(key))
        if text.strip():
            return text.strip()[-max_chars:]
    return _summary_from_transcript(
        str(hook_input.get("transcript_path") or ""), max_chars
    )


def extract_project_folder(hook_input: dict[str, Any]) -> str:
    """Workspace folder from any known key; first ``workspace_roots`` entry wins."""
    roots = hook_input.get("workspace_roots")
    if isinstance(roots, list):
        for item in roots:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for sub in ("path", "uri", "folder"):
                    val = item.get(sub)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
    for key in _PROJECT_KEYS:
        val = hook_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def extract_session_id(hook_input: dict[str, Any]) -> str:
    for key in _SESSION_KEYS:
        val = hook_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return ""


def state_dir() -> Path:
    """Runtime-state directory: ``<ENGRAM_DIR>/logs`` (fallback ``~/.engram/logs``)."""
    base = os.environ.get("ENGRAM_DIR", "").strip() or "~/.engram"
    return Path(base).expanduser() / "logs"


def debug_enabled() -> bool:
    return os.environ.get("ENGRAM_HOOK_DEBUG", "").strip().lower() in _TRUTHY


def debug_log(event: str, hook_input: dict[str, Any]) -> None:
    """Append one JSON line describing the payload *shape* (opt-in, local only).

    Records top-level keys and short value previews so a single real session
    is enough to confirm Cursor's actual field names. Never raises.
    """
    if not debug_enabled():
        return
    try:
        previews: dict[str, str] = {}
        for key, value in hook_input.items():
            if isinstance(value, str):
                previews[key] = value[:_PREVIEW_CHARS]
            else:
                try:
                    previews[key] = json.dumps(value, ensure_ascii=True)[:_PREVIEW_CHARS]
                except (TypeError, ValueError):
                    previews[key] = repr(value)[:_PREVIEW_CHARS]
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "keys": sorted(hook_input.keys()),
            "previews": previews,
        }
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with open(
            directory / "cursor_hooks_debug.log", "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    except Exception:
        pass


_STATE_FILE = "cursor_save_state.json"


def _load_state() -> dict[str, str]:
    try:
        raw = (state_dir() / _STATE_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def recently_saved(debounce_key: str, window_minutes: int) -> bool:
    """True when this session was saved within the window. Errors → False.

    Failing toward "save again" loses nothing (an extra context file) while
    failing toward "skip" could drop the only checkpoint of a session.
    """
    if window_minutes <= 0:
        return False
    last = _load_state().get(debounce_key)
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    return datetime.now() - last_ts < timedelta(minutes=window_minutes)


def mark_saved(debounce_key: str) -> None:
    """Record a save timestamp; prunes stale entries. Never raises."""
    try:
        state = _load_state()
        now = datetime.now()
        state[debounce_key] = now.isoformat(timespec="seconds")
        cutoff = now - timedelta(hours=_STATE_RETENTION_HOURS)
        pruned: dict[str, str] = {}
        for key, value in state.items():
            try:
                if datetime.fromisoformat(value) >= cutoff:
                    pruned[key] = value
            except (TypeError, ValueError):
                continue
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _STATE_FILE).write_text(
            json.dumps(pruned, ensure_ascii=True), encoding="utf-8"
        )
    except Exception:
        pass
