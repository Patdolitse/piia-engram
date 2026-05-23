#!/usr/bin/env python3
"""Claude Code Stop Hook: auto-save session context to Engram.

Triggered by Claude Code's Stop event. Reads the hook input from stdin
(contains transcript_path and cwd), extracts a lightweight session summary
from the transcript, and saves it to Engram via the Python API.

This is a complementary mechanism to the MCP server-side auto-tracking.
The MCP server tracks tool calls; this hook captures the conversation-level
metadata (duration, message count, working directory).

Usage in Claude Code settings.json:
    {
        "hooks": {
            "Stop": [{
                "hooks": [{
                    "type": "command",
                    "command": "python path/to/auto_save_on_stop.py",
                    "timeout": 30,
                    "async": true
                }]
            }]
        }
    }
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    # Read hook input from stdin
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

    # Extract basic transcript stats
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

                    # Capture timestamps
                    ts = entry.get("timestamp", "")
                    if ts and not first_ts:
                        first_ts = ts
                    if ts:
                        last_ts = ts

                    # Capture tool use names
                    if entry.get("type") == "tool_use":
                        name = entry.get("name", "")
                        if name and name not in tool_calls:
                            tool_calls.append(name)
                    # Also check content blocks for tool_use
                    for block in entry.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name and name not in tool_calls:
                                tool_calls.append(name)
        except OSError:
            pass

    # Don't save trivially short sessions
    if msg_count < 4:
        return

    # Calculate duration
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

    # Build content
    content = f"[Claude Code Stop Hook 自动记录]\n"
    content += f"工作目录: {cwd}\n"
    content += f"会话消息数: {msg_count}\n"
    content += f"会话时长: {duration_str}\n"
    if tool_calls:
        content += f"使用的工具: {', '.join(tool_calls[:30])}\n"

    # Save to Engram via Python API
    try:
        # Add engram source to path
        engram_src = Path(__file__).resolve().parent.parent / "src"
        if str(engram_src) not in sys.path:
            sys.path.insert(0, str(engram_src))

        from piia_engram.core import Engram

        engram = Engram()
        session_id = f"hook-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
        engram.save_agent_context(
            tool="claude_code",
            content=content,
            session_id=session_id,
            project_folder=cwd,
        )

        # Auto-update project snapshot with filesystem metrics
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
    except Exception:
        # Silent failure — hooks should never block Claude Code
        pass


if __name__ == "__main__":
    main()
