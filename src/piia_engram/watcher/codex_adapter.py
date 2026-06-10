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

from ._segments import read_segment

TOOL_NAME = "codex"

#: Override for the sessions root (tests, exotic layouts).
ENV_SESSIONS_ROOT = "ENGRAM_WATCH_CODEX_SESSIONS"


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


def _strip_extended_prefix(cwd: str) -> str:
    # Desktop writes extended-length paths (\\?\E:\...); strip the prefix so
    # context files carry the human-readable form.
    return cwd.strip().removeprefix("\\\\?\\")


def _read_head_meta(path: Path) -> tuple[str, str]:
    """Re-read ``(session_id, cwd)`` from the first transcript line.

    Incremental segments start mid-file, past the ``session_meta`` head line;
    without this re-read their checkpoints would fall back to the filename
    stem and split one conversation across two context files.
    """
    try:
        with open(path, "rb") as handle:
            first = handle.readline(65_536).decode("utf-8", errors="replace")
        entry = json.loads(first)
        payload = entry.get("payload")
        if entry.get("type") != "session_meta" or not isinstance(payload, dict):
            return "", ""
        sid = payload.get("id")
        cwd = payload.get("cwd")
        return (
            sid.strip() if isinstance(sid, str) else "",
            _strip_extended_prefix(cwd) if isinstance(cwd, str) else "",
        )
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return "", ""


def parse(path: Path, max_chars: int = 4000, start_offset: int = 0) -> dict:
    """Turn one rollout file (or its new tail) into a checkpoint payload.

    Returns ``{"session_id", "project_folder", "summary", "end_offset"}``.
    With ``start_offset > 0`` only lines appended since that byte position
    are summarized (incremental capture); ``end_offset`` is where the next
    scan should resume. ``session_id`` falls back to the filename stem so a
    transcript whose ``session_meta`` line was truncated away still maps to
    a stable per-conversation context file.
    """
    session_id = ""
    project_folder = ""
    lines: list[str] = []
    text, end_offset = read_segment(path, start_offset)

    for raw_line in text.splitlines():
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
                project_folder = _strip_extended_prefix(cwd)
        elif etype == "event_msg":
            ptype = payload.get("type")
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                continue
            if ptype == "user_message":
                lines.append(f"[user] {message.strip()}")
            elif ptype == "agent_message":
                lines.append(f"[assistant] {message.strip()}")

    if start_offset > 0 and not (session_id and project_folder):
        head_sid, head_cwd = _read_head_meta(path)
        session_id = session_id or head_sid
        project_folder = project_folder or head_cwd
    if not session_id:
        session_id = path.stem
    return {
        "session_id": session_id,
        "project_folder": project_folder,
        "summary": "\n".join(lines)[-max_chars:],
        "end_offset": end_offset,
    }
