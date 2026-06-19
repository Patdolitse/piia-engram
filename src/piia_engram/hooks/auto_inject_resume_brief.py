"""Claude Code SessionStart hook: auto-inject Engram resume brief.

v3.30 mechanism (6) — the "last mile" that makes cross-session and
cross-tool resume zero-effort. When Claude Code starts a new session,
this hook reads ``cwd`` from the stdin payload, asks Engram for
``get_resume_brief(project_folder=cwd)``, and emits the result via the
``hookSpecificOutput.additionalContext`` JSON protocol. Claude Code
splices that text into the system prompt for the first user turn, so
the AI knows what the previous session was doing without the user
having to say "接着上次".

Invoked as ``python -m piia_engram.hooks.auto_inject_resume_brief``.

Re-entry guard: if ``CLAUDE_INVOKED_BY=engram_recursive`` is set the
hook exits silently — the parent already produced the brief and a
child Claude Agent SDK invocation must not loop.
"""

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


def main() -> None:
    _apply_argv_env(sys.argv[1:])
    if os.environ.get("CLAUDE_INVOKED_BY") == "engram_recursive":
        print(json.dumps({"continue": True}))
        return
    if os.environ.get("CLAUDE_INVOKED_BY", "").startswith("engram_"):
        os.environ["CLAUDE_INVOKED_BY"] = "engram_recursive"

    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        hook_input = {}

    cwd = hook_input.get("cwd", "")

    try:
        from piia_engram.core import Engram
        engram = Engram()
        # Keep the budget snug so the additionalContext payload doesn't
        # dominate the first turn. 1500 tokens ≈ 6000 chars is plenty
        # for identity + project snapshot + a few lessons.
        brief = engram.get_resume_brief(
            project_folder=cwd or "",
            token_budget=1500,
        )
        markdown = brief.get("markdown", "")
        # Layer 3: append a once-per-week weekly hint. engram is left to default
        # so the helper builds a read_only (zero-write) instance for the recap.
        try:
            from ._weekly_hint import maybe_append_weekly_hint

            markdown = maybe_append_weekly_hint(markdown, project_folder=cwd or "")
        except Exception:
            pass
    except Exception as exc:
        log_failure("auto_inject_resume_brief", "get_resume_brief failed", exc)
        print(json.dumps({"continue": True}))
        return

    if not markdown:
        print(json.dumps({"continue": True}))
        return

    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": markdown,
        },
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
