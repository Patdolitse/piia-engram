"""Codex adapter: discover and summarize Codex rollout transcripts.

Codex (Desktop App and CLI alike — probed 2026-06-10, Desktop 26.x /
cli_version 0.137) persists every conversation as an append-only JSONL file::

    <codex_home>/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl

Line shapes that matter here:

- ``{"type": "session_meta", "payload": {"id": <uuid>, "cwd": <workspace>}}``
  — first line; carries the session id and working directory.
- ``{"type": "event_msg", "payload": {"type": "user_message", "message": str}}``
  — the legacy clean user input.
- ``{"type": "event_msg", "payload": {"type": "agent_message", "message": str}}``
  — the assistant's final reply for a turn.
- Current rollouts use ``response_item`` messages or ``item_completed``
  events containing ``UserMessage`` / ``AgentMessage`` items. Only text
  content parts are captured; injected instruction blocks are excluded.

Codex has no usable hook slot (its ``notify`` config slot is occupied by the
Codex App's own computer-use component), so this adapter feeds the universal
watcher instead. Everything here is strictly read-only on Codex data.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from ._segments import MAX_SEGMENT_BYTES, read_segment

TOOL_NAME = "codex"

#: Override for the sessions root (tests, exotic layouts).
ENV_SESSIONS_ROOT = "ENGRAM_WATCH_CODEX_SESSIONS"

# User-role response items can also carry host-injected instructions. Reject
# the whole contaminated part rather than attempting to interpret its markup.
_INJECTED_INSTRUCTIONS = re.compile(
    r"#\s*AGENTS\.md\b|"
    r"<(?:environment_context|skills_instructions|instructions|user_instructions|permissions_instructions|"
    r"permissions|system-reminder|skills)\b|"
    r"^\s*#{1,6}\s+(?:available\s+)?skills\b",
    re.IGNORECASE | re.MULTILINE,
)


# Host wrappers such as <collaboration_mode> or <user_action> are not the user's
# own words: a user part that starts with a tag-like wrapper is dropped.
_WRAPPED_PART = re.compile(r"\A\s*<[A-Za-z][\w.:-]*[\s>/]")


def _current_message(entry: dict) -> tuple[str, str, str] | None:
    """Return (source, role, text) for an allowlisted completed message."""
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    etype = entry.get("type")
    if etype == "response_item":
        source, item = "response", payload
    elif etype == "item_completed" or (
        etype == "event_msg" and payload.get("type") == "item_completed"
    ):
        source, item = "completed", payload.get("item", payload)
    else:
        return None
    if not isinstance(item, dict):
        return None
    itype = item.get("type")
    if itype in ("UserMessage", "AgentMessage"):
        role = "user" if itype == "UserMessage" else "assistant"
        if item.get("role", role) != role:
            return None
    elif itype == "message" and item.get("role") in ("user", "assistant"):
        role = item["role"]
    else:
        return None
    if item.get("channel") == "analysis" or item.get("phase") == "analysis":
        return None
    content = item.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype, text = part.get("type"), part.get("text")
        if not isinstance(ptype, str) or ptype.lower() not in ("input_text", "output_text", "text"):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and (_INJECTED_INSTRUCTIONS.search(text) or _WRAPPED_PART.match(text)):
            continue
        parts.append(text.strip())
    message = "\n".join(parts)
    return (source, role, message) if message else None


def _previous_messages(path: Path, offset: int) -> list[tuple[str, str, str]]:
    """Bounded look-behind for mirrors split across incremental scans."""
    seek = max(0, offset - MAX_SEGMENT_BYTES)
    with open(path, "rb") as handle:
        handle.seek(seek)
        raw = handle.read(offset - seek)
    if seek:
        raw = raw.partition(b"\n")[2]  # discard a potentially partial head
    messages: list[tuple[str, str, str]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        message = _current_message(entry)
        if message:
            messages.append(message)
        elif entry.get("type") == "event_msg":
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            role = "user" if ptype == "user_message" else "assistant" if ptype == "agent_message" else ""
            text = payload.get("message")
            if role and isinstance(text, str) and text.strip():
                messages.append(("legacy", role, text.strip()))
    return messages


def _current_lines(
    messages: list[tuple[str, str, str]], previous: list[tuple[str, str, str]]
) -> list[str]:
    # Count occurrences per source: mirror streams share an occurrence, while
    # repeated identical turns in the same stream must remain separate turns.
    counts: dict[tuple[str, str], dict[str, int]] = {}
    lines: list[str] = []
    for history, batch in ((True, previous), (False, messages)):
        for source, role, message in batch:
            sources = counts.setdefault((role, message), {})
            captured = max(sources.values(), default=0)
            sources[source] = sources.get(source, 0) + 1
            if not history and sources[source] > captured:
                lines.append(f"[{role}] {message}")
    return lines


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
    current: list[tuple[str, str, str]] = []
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
        message_item = _current_message(entry)
        if message_item:
            current.append(message_item)
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

    # Legacy clean events remain authoritative, including their exact ordering
    # and whitespace behaviour. Current formats are a fallback for new hosts.
    if not lines and current:
        previous = _previous_messages(path, start_offset) if 0 < start_offset <= end_offset else []
        lines = _current_lines(current, previous)

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
