"""Claude Code adapter: fallback capture from on-disk session transcripts.

Claude Code persists every conversation as an append-only JSONL file::

    ~/.claude/projects/<project-slug>/<session-uuid>.jsonl

Line shapes that matter here (probed 2026-06-10, Claude Code 2.x):

- ``{"type": "user", "message": {"content": str}, "sessionId", "cwd", ...}``
  — real user input when ``content`` is a string. List-shaped content is
  tool_result plumbing; ``isMeta``/``isSidechain`` lines and
  ``<command-...>``/``<local-command-...>`` slash-command echoes are noise.
- ``{"type": "assistant", "message": {"content": [{"type": "text", ...}]}}``
  — assistant turns; only ``text`` items are conversation (``thinking`` and
  ``tool_use`` items are skipped).

Claude Code *does* have a working Stop hook, and the hook is the better
channel (it fires exactly at end-of-turn). This adapter therefore yields to
it: :func:`discover` returns nothing when
``piia_engram.hooks.auto_save_on_stop`` is wired in the Claude settings, so
the adapter only activates for setups where the hook is absent (never ran
setup, hook removed, transcripts synced from another machine). Everything
here is strictly read-only on Claude Code data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from ._segments import read_segment

TOOL_NAME = "claude_code"

#: Override for the projects root (tests, exotic layouts).
ENV_PROJECTS_ROOT = "ENGRAM_WATCH_CLAUDE_PROJECTS"
#: Override for the Claude settings file checked by the hook yield (tests).
ENV_SETTINGS_FILE = "ENGRAM_WATCH_CLAUDE_SETTINGS"

_HOOK_MARKER = "piia_engram.hooks.auto_save_on_stop"
_NOISE_PREFIXES = ("<command-", "<local-command-", "<system-reminder>")


def projects_root() -> Path:
    override = os.environ.get(ENV_PROJECTS_ROOT, "").strip()
    if override:
        return Path(override).expanduser()
    return Path("~/.claude").expanduser() / "projects"


def _settings_files() -> list[Path]:
    override = os.environ.get(ENV_SETTINGS_FILE, "").strip()
    if override:
        return [Path(override).expanduser()]
    claude_dir = Path("~/.claude").expanduser()
    return [claude_dir / "settings.json", claude_dir / "settings.local.json"]


def hook_wired() -> bool:
    """True when the Claude Code Stop hook already captures sessions.

    A plain substring probe on the settings files — the hook command line
    always carries the module path, however it was wired (setup wizard or by
    hand). Unreadable settings count as "not wired": a double capture is
    cheap, a silently lost session is not.
    """
    for settings in _settings_files():
        try:
            if _HOOK_MARKER in settings.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def discover(since_days: int = 3) -> Iterator[Path]:
    """Yield transcripts modified within the window — unless the hook owns
    this tool (see module docstring), in which case nothing is yielded."""
    if hook_wired():
        return
    root = projects_root()
    if not root.is_dir():
        return
    cutoff = (datetime.now() - timedelta(days=max(1, since_days))).timestamp()
    try:
        project_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return
    for project_dir in project_dirs:
        try:
            entries = sorted(project_dir.glob("*.jsonl"))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_mtime >= cutoff:
                    yield entry
            except OSError:
                continue


def _user_line(content: str) -> str:
    stripped = content.strip()
    if not stripped or stripped.startswith(_NOISE_PREFIXES):
        return ""
    return f"[user] {stripped}"


def _assistant_lines(content: list) -> Iterator[str]:
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            yield f"[assistant] {text.strip()}"


def parse(path: Path, max_chars: int = 4000, start_offset: int = 0) -> dict:
    """Turn one transcript (or its new tail) into a checkpoint payload.

    Returns ``{"session_id", "project_folder", "summary", "end_offset"}``.
    Unlike Codex rollouts, every conversation line carries ``sessionId`` and
    ``cwd``, so mid-file segments are self-describing — no head re-read.
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
        if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
            continue
        if entry.get("isMeta") or entry.get("isSidechain"):
            continue
        sid = entry.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            session_id = sid.strip()
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            project_folder = cwd.strip()
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if entry["type"] == "user" and isinstance(content, str):
            line = _user_line(content)
            if line:
                lines.append(line)
        elif entry["type"] == "assistant" and isinstance(content, list):
            lines.extend(_assistant_lines(content))

    if not session_id:
        session_id = path.stem
    return {
        "session_id": session_id,
        "project_folder": project_folder,
        "summary": "\n".join(lines)[-max_chars:],
        "end_offset": end_offset,
    }
