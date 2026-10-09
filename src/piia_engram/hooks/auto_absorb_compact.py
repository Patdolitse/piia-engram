"""Claude Code PostCompact: queue a transcript reference for deferred daily-log archival. Semantic extraction remains outside this archival hook."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ._log import log_failure


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


def _extract_compact_summary(transcript_path: str, *, raise_errors: bool = False) -> str:
    """Extract the compact summary from the head of a compacted transcript.

    After Claude Code compaction the transcript JSONL is rewritten. The
    first non-empty entry that contains a text block of ≥200 chars is
    treated as the compact summary.  We look at up to the first 10
    entries and concatenate all ``text`` blocks from the first qualifying
    entry.

    Returns the extracted summary text, or "" if nothing qualifies.
    """
    tp = Path(transcript_path)

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
                if not isinstance(entry, dict):
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
        if raise_errors:
            raise

    return ""


def main() -> int:
    try:
        _apply_argv_env(sys.argv[1:])
        if os.environ.get("CLAUDE_INVOKED_BY") == "engram_recursive":
            return 0
        if os.environ.get("CLAUDE_INVOKED_BY", "").startswith("engram_"):
            os.environ["CLAUDE_INVOKED_BY"] = "engram_recursive"
        from ._producer import claude_event
        return claude_event("claude_compact")
    except Exception as exc:
        log_failure("auto_absorb_compact", "hook failed (" + type(exc).__name__ + ")")
        return 0

if __name__ == "__main__":
    main()
