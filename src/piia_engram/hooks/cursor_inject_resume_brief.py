"""Cursor sessionStart: read-only resume injection with a fixed application deadline. Failure or timeout returns the compatible continue response."""

from __future__ import annotations

import json
import os
import sys

from . import _cursor_payload as payload
from ._log import log_failure

_ACTIVE_ENV = "ENGRAM_CURSOR_INJECT_ACTIVE"
_TOKEN_BUDGET = 1500


def _passthrough() -> int:
    print(json.dumps({"continue": True}))
    return 0


def main() -> int:
    if os.environ.get(_ACTIVE_ENV) == "1":
        return _passthrough()
    os.environ[_ACTIVE_ENV] = "1"
    try:
        payload.apply_argv_env(sys.argv[1:])
        payload.reconfigure_stdin_utf8()
        from ._budget import read_with_budget

        def read():
            from piia_engram.core import Engram
            from ._producer import _read_input
            project = payload.extract_project_folder(_read_input())
            brief = Engram(read_only=True).get_resume_brief(
                project_folder=project, token_budget=_TOKEN_BUDGET)
            return str(brief.get("markdown", "") or "")

        markdown = read_with_budget(read, "cursor_inject_resume_brief")
        if not markdown.strip():
            return _passthrough()
        print(json.dumps({"continue": True, "additional_context": markdown,
                          "hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": markdown}}, ensure_ascii=True))
        return 0
    except Exception as exc:
        log_failure("cursor_inject_resume_brief", "hook failed (" + type(exc).__name__ + ")")
        return _passthrough()
    finally:
        os.environ.pop(_ACTIVE_ENV, None)

if __name__ == "__main__":
    raise SystemExit(main())
