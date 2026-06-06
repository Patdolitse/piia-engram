# Cursor setup

Use this card when Cursor should access Engram through MCP.

## Configure

Run the wizard:

```bash
pip install piia-engram
engram setup
```

To let Engram write backed-up external config changes, use:

```bash
engram setup --apply-external-config
```

Manual MCP entries should launch:

```bash
python -m piia_engram.mcp_server
```

Leave `ENGRAM_TOOLS` unset for first value. Enable `ENGRAM_TOOLS=all` only when
you need advanced management surfaces.

## Smoke test

1. Restart Cursor.
2. Confirm the MCP panel shows the Engram server connected.
3. Ask Cursor to call `get_user_context` or `get_resume_brief`.
4. Ask it to search for a known verified lesson with `search_knowledge`.

This supports an L2 read/search claim when the tool calls and answer are
captured. Do not claim L3 behavior gain or L4 cross-client continuity until a
validation run includes A/B controls, raw/parsed artifacts, and zero-pollution
evidence.

## Known seams

Cursor plugin packaging and skill-path behavior can vary by version. Record the
client version and exact config form in validation evidence. See
[Cursor plugin validation](../cursor-plugin-validation.md) for the detailed
test sheet, and keep the [operator MCP cheatsheet](../operator-mcp-cheatsheet.md)
nearby when deciding whether to enable all tools.
