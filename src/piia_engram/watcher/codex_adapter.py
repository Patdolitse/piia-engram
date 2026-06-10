"""Codex adapter: discover and summarize Codex rollout transcripts.

Codex (Desktop App and CLI alike — probed 2026-06-10, Desktop 26.x /
cli_version 0.137) persists every conversation as an append-only JSONL file::

    <codex_home>/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl

Line shapes that matter here:

- ``{"type": "session_meta", "payload": {"id": <uuid>, "cwd": <workspace>}}``
  — first line; carries the session id and working directory.
- ``{"type": "event_msg", "payload": {"type": "user_message", "message": str}}``
  — the *clean* user input. (``response_item`` user messages are polluted by
  AGENTS.md / permissions injections, so they are deliberately ignored.)
- ``{"type": "event_msg", "payload": {"type": "agent_message", "message": str}}``
  — the assistant's final reply for a turn.

Codex has no usable hook slot (its ``notify`` config slot is occupied by the
Codex App's own computer-use component), so this adapter feeds the universal
watcher instead. Everything here is strictly read-only on Codex data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

TOOL_NAME = "codex"

#: Override for the sessions root (tests, exotic layouts).
ENV_SESSIONS_ROOT = "ENGRAM_WATCH_CODEX_SESSIONS"

#: Cap on how much of a large transcript is read (tail wins — conclusions
#: beat openings; same rationale as the Cursor hook transcript reader).
_MAX_TRANSCRIPT_BYTES = 512_000


def sessions_root() -> Path:
    """Resolve the Codex sessions directory.

    Priority: explicit ``ENGRAM_WATCH_CODEX_SESSIONS`` override, then
    ``CODEX_HOME/sessions``, then ``~/.codex/sessions``.
    """
    override = os.environ.get(ENV_SESSIONS_ROOT, "").strip()
    if override:
        return Path(override).expanduser()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "sessions"
    return Path("~/.codex").expanduser() / "sessions"


def discover(since_days: int = 3) -> Iterator[Path]:
    """Yield rollout files from the last ``since_days`` date directories.

    The ``YYYY/MM/DD`` layout makes date-bounded discovery cheap: walk only
    the day directories inside the window instead of the whole tree (the
    sessions tree can hold hundreds of historical conversations).
    """
    root = sessions_root()
    if not root.is_dir():
        return
    today = datetime.now().date()
    for offset in range(max(1, since_days)):
        day = today - timedelta(days=offset)
        day_dir = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if not day_dir.is_dir():
            continue
        try:
            entries = sorted(day_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name.startswith("rollout-") and entry.suffix == ".jsonl":
                yield entry


def _read_tail(path: Path) -> str:
    """Read the transcript, keeping only the tail of oversized files."""
    try:
        size = path.stat().st_size
        if size > _MAX_TRANSCRIPT_BYTES:
            with open(path, "rb") as handle:
                handle.seek(size - _MAX_TRANSCRIPT_BYTES)
                raw = handle.read().decode("utf-8", errors="replace")
            # First line is likely cut mid-JSON; drop it.
            return raw.split("\n", 1)[-1]
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse(path: Path, max_chars: int = 4000) -> dict[str, str]:
    """Turn one rollout file into a checkpoint payload.

    Returns ``{"session_id", "project_folder", "summary"}`` (any field may be
    empty). ``session_id`` falls back to the filename stem so a transcript
    whose ``session_meta`` line was truncated away still maps to a stable
    per-conversation context file.
    """
    session_id = ""
    project_folder = ""
    lines: list[str] = []

    for raw_line in _read_tail(path).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        etype = entry.get("type")
        if etype == "session_meta":
            sid = payload.get("id")
            if isinstance(sid, str) and sid.strip():
                session_id = sid.strip()
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                # Desktop writes extended-length paths (\\?\E:\...); strip the
                # prefix so context files carry the human-readable form.
                project_folder = cwd.strip().removeprefix("\\\\?\\")
        elif etype == "event_msg":
            ptype = payload.get("type")
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                continue
            if ptype == "user_message":
                lines.append(f"[user] {message.strip()}")
            elif ptype == "agent_message":
                lines.append(f"[assistant] {message.strip()}")

    if not session_id:
        session_id = path.stem
    return {
        "session_id": session_id,
        "project_folder": project_folder,
        "summary": "\n".join(lines)[-max_chars:],
    }
