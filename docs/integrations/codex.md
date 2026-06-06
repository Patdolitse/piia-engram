# Codex setup

Use this card when Codex should read the same local Engram store as Claude Code,
Cursor, or another MCP-compatible tool.

## Configure

Run the setup wizard:

```bash
pip install piia-engram
engram setup
```

If you want Engram to update external client config files, use the explicit
opt-in command:

```bash
engram setup --apply-external-config
```

Manual MCP entries should launch:

```bash
python -m piia_engram.mcp_server
```

Keep the default core surface for daily use. `ENGRAM_TOOLS=all` is for advanced
review, import/export, local tool registry, and governance workflows.

## Smoke test

1. Restart Codex.
2. Ask Codex to call `get_resume_brief` or `get_user_context`.
3. Ask it to search for a known verified lesson with `search_knowledge`.
4. Confirm it reports the lesson without printing unrelated private store
   content.

Passing this smoke test supports an L2 read/search claim for Codex. The live
Claude Code -> Codex continuity proof is an L4 partial proof for that specific
handoff, not a universal benchmark for every client.

## Boundaries

Read/search calls are normal session context. Writes, exports, public actions,
and all-tool mode still require the user's intent and the relevant owner gates.
See the [operator MCP cheatsheet](../operator-mcp-cheatsheet.md).
