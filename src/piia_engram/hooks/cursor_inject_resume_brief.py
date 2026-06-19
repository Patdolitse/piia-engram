"""Cursor sessionStart hook: auto-inject the Engram resume brief.

Cursor-side twin of ``auto_inject_resume_brief`` (Claude Code). When a Cursor
session starts, this hook reads the workspace folder from the stdin payload,
asks Engram for ``get_resume_brief(project_folder=...)``, and prints the brief
as JSON on stdout so Cursor can splice it into the session context.

Cursor's sessionStart *output* schema is not fully documented, so the hook
emits two candidate shapes in one JSON object — a flat ``additional_context``
field (matching Cursor's documented preToolUse convention) and a Claude-style
``hookSpecificOutput.additionalContext`` block. Unknown fields are ignored by
JSON consumers, so carrying both is harmless and doubles the chance the brief
lands without a protocol-adaptation round-trip. ``ENGRAM_HOOK_DEBUG=1``
records the incoming payload shape for that adaptation (see
``_cursor_payload.debug_log``).

Invoked as ``python -m piia_engram.hooks.cursor_inject_resume_brief``.
Fail-silent by contract: any failure prints ``{"continue": true}`` and exits 0
so a broken hook can never block a Cursor session.
"""

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
    payload.apply_argv_env(sys.argv[1:])
    payload.reconfigure_stdin_utf8()

    # Re-entry guard: a child invocation triggered while this hook is already
    # running must not recurse into another brief generation.
    if os.environ.get(_ACTIVE_ENV) == "1":
        return _passthrough()
    os.environ[_ACTIVE_ENV] = "1"

    try:
        hook_input = payload.read_hook_input()
        payload.debug_log("sessionStart", hook_input)

        project_folder = payload.extract_project_folder(hook_input)

        try:
            from piia_engram.core import Engram

            brief = Engram().get_resume_brief(
                project_folder=project_folder,
                token_budget=_TOKEN_BUDGET,
            )
            markdown = str(brief.get("markdown", "") or "")
            # Layer 3: append a once-per-week weekly hint. engram is left to default
            # so the helper builds a read_only (zero-write) instance for the recap.
            try:
                from ._weekly_hint import maybe_append_weekly_hint

                markdown = maybe_append_weekly_hint(markdown, project_folder=project_folder)
            except Exception:
                pass
        except Exception as exc:
            log_failure("cursor_inject_resume_brief", "get_resume_brief failed", exc)
            return _passthrough()

        if not markdown.strip():
            return _passthrough()

        output = {
            "continue": True,
            # Candidate 1: flat snake_case (Cursor's documented hook-output style).
            "additional_context": markdown,
            # Candidate 2: Claude Code-style block, kept for compatibility.
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": markdown,
            },
        }
        # ensure_ascii=True: \uXXXX escapes survive any Windows console
        # codepage; JSON consumers decode them losslessly.
        print(json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:
        log_failure("cursor_inject_resume_brief", "hook failed", exc)
        return _passthrough()
    finally:
        os.environ.pop(_ACTIVE_ENV, None)


if __name__ == "__main__":
    raise SystemExit(main())
