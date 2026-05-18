<div align="center">

<img src="assets/social_preview.png" alt="Engram - Local AI memory layer" width="640">

# Engram

### A local memory layer for AI coding tools

**Stop re-explaining yourself every time you switch tools, projects, or sessions.**

`Claude Code` | `Codex` | `Cursor` | `MCP compatible` | `100% local`

[English](README.md) | [中文](README.zh-CN.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

</div>

---

AI coding tools are powerful, but they do not really know you.

Every time you switch from Claude Code to Codex, open Cursor, start a new session, or move into a different project, you often have to explain the same things again:

- how you prefer to communicate
- how the AI should approach code
- which project rules matter
- which mistakes should not happen again
- why earlier decisions were made

Engram stores that collaboration memory as local files, then exposes it through MCP so different AI tools can read the same user context.

The goal is simple: **make every compatible AI tool start from the same understanding of you.**

## Why Engram?

| Without Engram | With Engram |
|---|---|
| Every new session starts from zero | AI tools can load your identity and preferences |
| Switching tools loses accumulated context | Claude Code, Codex, and Cursor can read the same memory |
| Project rules live in scattered prompts | Rules and decisions are stored as local assets |
| Past mistakes get repeated | Lessons learned can follow you across tools |
| Memory is locked inside one product | Data stays local, editable, and portable |

Engram is not another chat app, agent framework, or hosted memory service. It is a small local memory layer that sits underneath the tools you already use.

## What It Stores

Engram can store:

- identity and communication preferences
- coding style and quality standards
- trust boundaries for AI tools
- project snapshots
- lessons learned
- key technical or product decisions
- domain knowledge that should be reused later

All data is stored under `~/.engram/` as JSON and Markdown files. You can open, edit, back up, or migrate it yourself.

## Quick Start

```bash
git clone https://github.com/Patdolitse/engram.git
cd engram
pip install -e .
python demos/setup_engram.py
```

Then configure Engram as an MCP server in your AI coding tool.

Example MCP config:

```json
{
  "mcpServers": {
    "engram": {
      "command": "python",
      "args": ["/path/to/engram/src/engram_core/mcp_server.py"]
    }
  }
}
```

After restarting your MCP-compatible client, a new session can call `get_user_context` to understand your profile, preferences, lessons, and project context.

## MCP Tools

Engram exposes read, write, project, backup, and compatibility tools through MCP.

Common tools include:

| Tool | Purpose |
|---|---|
| `get_user_context` | Load the complete user context at the start of a session |
| `get_identity_card` | Export a Markdown identity card for tools without MCP |
| `get_profile` | Read the user profile |
| `get_preferences` | Read communication and workflow preferences |
| `get_trust_boundaries` | Read data access boundaries |
| `get_quality_standards` | Read quality expectations |
| `get_lessons` | Read reusable lessons learned |
| `get_decisions` | Read key decisions and reasons |
| `get_relevant_knowledge` | Find knowledge relevant to a project |
| `save_project_snapshot` | Save project context for later sessions |
| `add_lesson` | Add a lesson learned |
| `add_decision` | Add a key decision |
| `export_engram` | Export a full backup |
| `import_engram` | Import a backup |
| `export_engram_to_openclaw` | Export OpenClaw-compatible files |
| `import_engram_from_openclaw` | Import OpenClaw-compatible files |
| `search_knowledge` | Search lessons and decisions by keyword |
| `get_health_report` | Knowledge asset health report (duplicates, capacity, warnings) |
| `update_lesson` | Update a lesson (summary, domain, status) |
| `archive_lesson` | Mark a lesson as outdated |
| `update_decision` | Update a decision |
| `archive_decision` | Mark a decision as outdated |

## Data Layout

```text
~/.engram/
|-- schema_version.json
|-- identity/
|   |-- profile.json
|   |-- preferences.json
|   |-- quality_standards.json
|   `-- trust_boundaries.json
|-- knowledge/
|   |-- lessons.json
|   |-- decisions.json
|   `-- domains.json
|-- projects/
|   `-- {project_id}.json
|-- exports/
`-- compat/
    `-- openclaw/
```

## Supported Tools

| Tool | Integration | Status |
|---|---|---|
| Claude Code | MCP over stdio | Tested |
| Codex | MCP over stdio | Tested |
| Cursor | MCP over stdio | Expected to work |
| Claude Desktop | MCP over stdio | Expected to work |
| OpenClaw | SOUL.md / MEMORY.md / USER.md import and export | Tested |
| ChatGPT / Gemini / Kimi | Markdown identity card fallback | Usable |

## Comparison

| Feature | Engram | Claude Memory | Manual `CLAUDE.md` | Mem0 |
|---|---|---|---|---|
| Cross-tool sharing | Yes | Claude only | Tool-specific | Yes |
| Local storage | Yes | Cloud | Local | Cloud / hosted |
| Directly editable data | JSON / Markdown | Not visible | Yes | API-based |
| MCP standard | Yes | Not applicable | Not applicable | Yes |
| Portable backup | Copy files or export JSON | Limited | Copy files | API export |
| Model-agnostic | Yes | Claude-focused | Depends on the tool | Yes |
| Price | Free and open source | Included in subscription | Free | Free / paid tiers |

## Built With

Engram is a human-directed, AI-assisted open-source project.

| Contributor | Role |
|---|---|
| [@Patdolitse](https://github.com/Patdolitse) | Creator, product direction, strategy, ownership |
| Claude Code | Architecture, task planning, code review assistance |
| Codex | Implementation, testing, documentation assistance |

## Contributing

Contributions, issues, and feedback are welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md). Chinese readers can also use [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md).

## License

[Apache 2.0](LICENSE). Engram is free software. Your memory belongs to you.
