# Engram — Cursor plugin (skeleton)

This is a **minimal, honest skeleton** that wires Cursor to
[Engram](https://github.com/Patdolitse/piia-engram), a local-first personal AI
identity and memory layer for MCP-compatible coding tools. It is an
ecosystem entry point, not a finished product surface.

## What it does

- Declares the Engram MCP server so Cursor can launch it over stdio via the
  `piia-engram-mcp` command.
- Points at the `engram` Agent Skill (`skills/engram/SKILL.md`) for guidance on
  *when* to use Engram and *which* MCP tools to call.

## What it does not do

- It does not bundle credentials, write to your config without consent, or send
  anything to a cloud. Engram is local-first; telemetry is opt-in only.
- It does not claim capabilities Engram does not have today.

## Prerequisites

Install Engram so the `piia-engram-mcp` command is on your PATH:

```bash
pip install piia-engram
# or run without installing:
# uvx --from piia-engram piia-engram-mcp
```

## Manifest

See [`plugin.json`](plugin.json). The MCP server is declared as:

```json
"mcpServers": {
  "engram": {
    "type": "stdio",
    "command": "piia-engram-mcp"
  }
}
```

By default Engram exposes a Tier-1 core tool set. To expose the full tool set,
launch the server with the environment variable `ENGRAM_TOOLS=all` (configure
this in your MCP client / plugin environment when supported).

## Known uncertainties (skeleton)

The exact Cursor plugin schema is still evolving. The following fields are our
best-effort mapping and may need adjustment against the current Cursor plugin
schema:

- **`skills` path resolution** — set to `../skills/` so it resolves to the
  repository's `skills/` directory from this `.cursor-plugin/` folder. Depending
  on how Cursor resolves plugin-relative paths, this may need to become
  `./skills/` (a copy inside the plugin) instead.
- **`version`** — set to `0.1.0` to reflect skeleton maturity; it is
  intentionally decoupled from the Engram package release version.
- **`mcpServers` inline form** — declared inline here. If your Cursor version
  expects a separate `mcp.json` reference, move the block accordingly.

Treat these as the documented seams to validate against a live Cursor install.
The manual validation checklist is in
[`docs/cursor-plugin-validation.md`](../docs/cursor-plugin-validation.md); run it
before this skeleton is ever proposed for publication.
