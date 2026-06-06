# Claude Code setup

Use this card when Claude Code should read the same local Engram store as your
other MCP-compatible tools.

## Configure

Run the wizard first:

```bash
pip install piia-engram
engram setup
```

If you want the wizard to write the MCP entry for you, use the explicit opt-in
path:

```bash
engram setup --apply-external-config
```

Manual MCP entries should launch:

```bash
python -m piia_engram.mcp_server
```

Leave `ENGRAM_TOOLS` unset for the default 17 core tools. Add
`ENGRAM_TOOLS=all` only when you intentionally need review, import/export,
tool-registry, or governance maintenance surfaces.

## Smoke test

1. Restart Claude Code.
2. Ask it to call `get_resume_brief` or `get_user_context`.
3. Save one low-risk preference with `memory_store` or `add_lesson`.
4. Open a fresh session and ask it to search for that preference.

Passing this smoke test supports an L2 read/search claim for Claude Code. A
cross-client claim needs L4 evidence: another client must cold-start and recall
the marker without you restating it.

## Boundaries

Core is not read-only. Some core tools write local memory, and
`get_identity_card` is an owner-gated export surface. See the
[operator MCP cheatsheet](../operator-mcp-cheatsheet.md) before enabling all
tools or publishing evidence.
