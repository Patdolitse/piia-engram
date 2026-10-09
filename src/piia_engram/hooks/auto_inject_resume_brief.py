"""Claude Code SessionStart: synchronously return a read-only resume brief within a fixed application deadline, or continue with no additional context."""

from __future__ import annotations

import json
import os
import sys

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


def main() -> int:
    try:
        _apply_argv_env(sys.argv[1:])
        if os.environ.get("CLAUDE_INVOKED_BY") == "engram_recursive":
            print(json.dumps({"continue": True}))
            return 0
        if os.environ.get("CLAUDE_INVOKED_BY", "").startswith("engram_"):
            os.environ["CLAUDE_INVOKED_BY"] = "engram_recursive"
        from ._budget import read_with_budget

        def read():
            from ._producer import _read_input
            from piia_engram.core import Engram
            cwd = _read_input().get("cwd", "")
            brief = Engram(read_only=True).get_resume_brief(
                project_folder=cwd if isinstance(cwd, str) else "", token_budget=1500)
            return str(brief.get("markdown", "") or "")

        markdown = read_with_budget(read, "auto_inject_resume_brief")
        output = {"continue": True}
        if markdown:
            output["hookSpecificOutput"] = {"hookEventName": "SessionStart",
                                             "additionalContext": markdown}
        print(json.dumps(output, ensure_ascii=True))
    except Exception as exc:
        log_failure("auto_inject_resume_brief", "hook failed (" + type(exc).__name__ + ")")
        print(json.dumps({"continue": True}))
    return 0

if __name__ == "__main__":
    main()
