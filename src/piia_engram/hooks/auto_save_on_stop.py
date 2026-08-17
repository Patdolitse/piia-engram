"""Claude Code Stop / PreCompact hook: auto-save session context to Engram.

Invoked as ``python -m piia_engram.hooks.auto_save_on_stop``. Reads the
hook input from stdin (containing transcript_path and cwd), extracts a
lightweight session summary from the transcript, and saves it via the
``Engram`` Python API.

This is the conversation-level companion to the MCP server-side
heartbeat tracker. The MCP server snapshots tool-call state; this hook
captures end-of-session metadata (duration, message count, cwd).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ._log import log_failure


def _apply_argv_env(argv: list[str]) -> None:
    """Promote ``--env KEY=VAL`` argv pairs into ``os.environ``.

    Used by ``setup_wizard`` to transport env hints (e.g.
    ``ENGRAM_MIN_TURNS_TO_FLUSH=5``) cross-platform — Windows shells
    don't accept the ``KEY=VAL prog`` inline prefix that POSIX shells
    do, so we ride the args instead.
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


def _flush_threshold() -> int:
    """Return ``ENGRAM_MIN_TURNS_TO_FLUSH`` (default 10).

    The PreCompact hook (mechanism 4) sets this to 5 so short sessions
    skip flush at every minor auto-compaction. The Stop hook leaves the
    default 10 because a session that finished organically deserves a
    flush regardless of length.
    """
    raw = os.environ.get("ENGRAM_MIN_TURNS_TO_FLUSH", "10")
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return 10


def main() -> None:
    _apply_argv_env(sys.argv[1:])
    # Re-entry guard. The Claude Agent SDK invocation we trigger below
    # can itself fire SessionEnd/PreCompact in a child process, which
    # would loop infinitely. ``CLAUDE_INVOKED_BY=engram_*`` is set by the
    # parent hook command to break the recursion cycle.
    # Recursion is BOUNDED AT DEPTH TWO by design: an engram_* child runs
    # once (upgraded to engram_recursive), and only that child's own child
    # returns immediately. One nested save is accepted; infinite loops are not.
    if os.environ.get("CLAUDE_INVOKED_BY", "").startswith("engram_"):
        if os.environ.get("CLAUDE_INVOKED_BY") == "engram_recursive":
            return
        os.environ["CLAUDE_INVOKED_BY"] = "engram_recursive"

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    cwd = hook_input.get("cwd", "")
    transcript_path = hook_input.get("transcript_path", "")
    session_id_raw = hook_input.get("session_id", "")

    msg_count = 0
    tool_calls: list[str] = []
    first_ts = ""
    last_ts = ""

    if transcript_path and Path(transcript_path).exists():
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_count += 1

                    ts = entry.get("timestamp", "")
                    if ts and not first_ts:
                        first_ts = ts
                    if ts:
                        last_ts = ts

                    if entry.get("type") == "tool_use":
                        name = entry.get("name", "")
                        if name and name not in tool_calls:
                            tool_calls.append(name)
                    for block in entry.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name and name not in tool_calls:
                                tool_calls.append(name)
        except OSError:
            pass

    # Skip trivially short sessions. Threshold is env-configurable so
    # PreCompact can lower it (mechanism 4).
    min_save_threshold = max(4, _flush_threshold() // 2)
    if msg_count < min_save_threshold:
        return

    duration_str = "unknown"
    if first_ts and last_ts:
        try:
            fmt_candidates = [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
            ]
            t_start = t_end = None
            for fmt in fmt_candidates:
                try:
                    t_start = datetime.strptime(first_ts, fmt)
                    break
                except ValueError:
                    continue
            for fmt in fmt_candidates:
                try:
                    t_end = datetime.strptime(last_ts, fmt)
                    break
                except ValueError:
                    continue
            if t_start and t_end:
                mins = max(1, int((t_end - t_start).total_seconds() / 60))
                duration_str = f"{mins} 分钟"
        except Exception:
            pass

    content = "[Claude Code Stop Hook 自动记录]\n"
    content += f"工作目录: {cwd}\n"
    content += f"会话消息数: {msg_count}\n"
    content += f"会话时长: {duration_str}\n"
    if tool_calls:
        content += f"使用的工具: {', '.join(tool_calls[:30])}\n"

    try:
        from piia_engram.core import Engram

        engram = Engram()
        session_id = f"hook-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
        engram.save_agent_context(
            tool="claude_code",
            content=content,
            session_id=session_id,
            project_folder=cwd,
        )

        if msg_count >= _flush_threshold():
            # checkpoint_content above stays metadata-only by construction.
            # The extraction summary may additionally carry an OPT-IN
            # sanitized assistant-text digest (hook_content_digest preference,
            # default off; privacy contract in piia_engram.hook_digest).
            summary = f"Claude Code 会话 ({duration_str}, {msg_count} 消息)\n"
            summary += f"工作目录: {cwd}\n"
            if tool_calls:
                summary += f"使用工具: {', '.join(tool_calls[:20])}\n"
            digest_appended = False
            try:
                from piia_engram.hook_digest import (
                    PREFERENCE_KEY,
                    build_digest,
                    digest_enabled,
                    read_transcript_lines,
                )

                prefs = engram.get_preferences()
                if digest_enabled(prefs.get(PREFERENCE_KEY)) and transcript_path:
                    digest = build_digest(read_transcript_lines(transcript_path))
                    if digest:
                        summary = summary + "\n" + digest
                        digest_appended = True
            except Exception as exc:
                log_failure("auto_save_on_stop", "content digest build failed", exc)
            try:
                # Unsupervised background writeback: force_staging keeps every
                # extracted item behind owner review (trust-model contract);
                # a digest-carrying call also runs the output privacy guard.
                engram.extract_session_insights(
                    summary,
                    source_tool="claude_code",
                    source_ref=session_id,
                    force_staging=True,
                    project_folder=cwd or "",
                    capture_origin="hook_content_digest" if digest_appended else "",
                )
            except Exception as exc:
                log_failure("auto_save_on_stop", "extract_session_insights failed", exc)
            try:
                # Keep the Layer-1 cold-start snapshot current so the next
                # session's quick path reflects this session's memory.
                engram.refresh_quick_context()
            except Exception as exc:
                log_failure("auto_save_on_stop", "quick_context refresh failed", exc)

        if cwd:
            _root = Path(cwd)
            _pyproject = _root / "pyproject.toml"
            if _pyproject.is_file():
                import re as _re

                snap: dict = {}
                try:
                    _text = _pyproject.read_text(encoding="utf-8")
                    _m = _re.search(
                        r'^version\s*=\s*"([^"]+)"', _text, _re.MULTILINE,
                    )
                    if _m:
                        snap["version"] = _m.group(1)
                except Exception:
                    pass
                try:
                    _src = _root / "src"
                    if _src.is_dir():
                        snap["module_count"] = sum(
                            1 for p in _src.rglob("*.py")
                            if "__pycache__" not in str(p)
                        )
                except Exception:
                    pass
                try:
                    _tests = _root / "tests"
                    if _tests.is_dir():
                        _tc = 0
                        for _tf in _tests.rglob("*.py"):
                            if "__pycache__" in str(_tf):
                                continue
                            try:
                                for _ln in _tf.read_text(encoding="utf-8").splitlines():
                                    _s = _ln.lstrip()
                                    if _s.startswith("def test_") or _s.startswith("async def test_"):
                                        _tc += 1
                            except Exception:
                                continue
                        snap["test_count"] = _tc
                except Exception:
                    pass
                if snap:
                    snap["last_auto_snapshot"] = datetime.now().isoformat()
                    engram.save_project_snapshot(cwd, snap)
    except Exception as exc:
        # Hooks must never block Claude Code — but failures must not be
        # invisible either: leave a breadcrumb in hooks.log.
        log_failure("auto_save_on_stop", "session save failed", exc)


if __name__ == "__main__":
    main()
