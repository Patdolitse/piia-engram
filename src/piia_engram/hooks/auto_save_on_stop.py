"""Claude Code Stop / PreCompact: publish a local event and exit. Transcript scanning, checkpoints, staging extraction and project snapshots run only in the explicit offline processor."""

from __future__ import annotations

import os
import sys

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


def main() -> int:
    try:
        _apply_argv_env(sys.argv[1:])
        if os.environ.get("CLAUDE_INVOKED_BY") == "engram_recursive":
            return 0
        if os.environ.get("CLAUDE_INVOKED_BY", "").startswith("engram_"):
            os.environ["CLAUDE_INVOKED_BY"] = "engram_recursive"
        from ._producer import claude_event
        return claude_event("claude_stop", _flush_threshold())
    except Exception as exc:
        log_failure("auto_save_on_stop", "hook failed (" + type(exc).__name__ + ")")
        return 0

if __name__ == "__main__":
    main()
