"""Claude Code PostCompact hook: absorb compact summary into Engram daily log.

v3.30 mechanism (R4) — fires AFTER Claude Code compacts the transcript.
The compacted transcript's opening entry is an AI-generated summary of
everything that was thrown away; this hook captures that summary and
appends it to the per-project daily log so it survives across sessions.

v3.31 P0-2: this hook is intentionally narrow now — daily log archival
only. The companion ``agent`` hook (configured via ``setup_wizard``'s
PostCompact registration) owns semantic extraction (lessons/decisions
via LLM reasoning). Previously this script ALSO called
``extract_session_insights`` which duplicated the agent hook's work and
double-wrote the staging tier. Now: agent → semantic lessons/decisions;
this script → raw daily log preservation. They no longer step on each
other.

Invoked as ``python -m piia_engram.hooks.auto_absorb_compact``.

Re-entry guard: exits silently when ``CLAUDE_INVOKED_BY`` starts with
``engram_`` (same recursion-break protocol as the other Engram hooks).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _apply_argv_env(argv: list[str]) -> None:
    """Promote ``--env KEY=VAL`` argv pairs into ``os.environ``."""
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


def _extract_compact_summary(transcript_path: str) -> str:
    """Extract the compact summary from the head of a compacted transcript.

    After Claude Code compaction the transcript JSONL is rewritten. The
    first non-empty entry that contains a text block of ≥200 chars is
    treated as the compact summary.  We look at up to the first 10
    entries and concatenate all ``text`` blocks from the first qualifying
    entry.

    Returns the extracted summary text, or "" if nothing qualifies.
    """
    tp = Path(transcript_path)
    if not tp.exists():
        return ""

    try:
        with tp.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 10:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Look for content blocks with substantial text
                content = entry.get("content", [])
                if isinstance(content, str) and len(content) >= 200:
                    return content

                if isinstance(content, list):
                    texts: list[str] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                    combined = "\n".join(texts)
                    if len(combined) >= 200:
                        return combined
    except OSError:
        pass

    return ""


def main() -> None:
    _apply_argv_env(sys.argv[1:])

    # Re-entry guard — same protocol as auto_save_on_stop.py
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

    if not transcript_path:
        return

    summary = _extract_compact_summary(transcript_path)
    if not summary:
        return

    # Truncate at 3000 chars to keep daily log entries reasonable.
    # Compact summaries can be very long for sessions that accumulated
    # thousands of turns before compaction.
    MAX_SUMMARY_CHARS = 3000
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n\n…（已截断）"

    try:
        from piia_engram.core import Engram

        engram = Engram()

        # 1. Append to daily log as a "compact" event
        content = "[PostCompact Hook 自动记录]\n"
        content += f"工作目录: {cwd}\n"
        content += f"压缩摘要长度: {len(summary)} 字符\n\n"
        content += summary

        engram.append_daily_log(
            project_folder=cwd or "",
            content=content,
            event_type="compact",
            source_tool="claude_code",
        )

        # v3.31 P0-2: semantic extraction (lessons/decisions) is now the
        # exclusive responsibility of the PostCompact ``agent`` hook
        # registered alongside this command hook. Calling
        # extract_session_insights here would double-write the staging
        # tier — see hooks audit decision for v3.31.

    except Exception:
        # Hooks must never block Claude Code.
        pass


if __name__ == "__main__":
    main()
