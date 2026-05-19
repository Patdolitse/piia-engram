<div align="center">

<img src="assets/social_preview.png" alt="Engram - Local AI memory layer" width="640">

# Engram

### A local memory layer for AI coding tools

**Stop re-explaining yourself every time you switch tools, projects, or sessions.**

`Claude Code` | `Codex` | `Cursor` | `MCP compatible` | `100% local`

[ENGLISH](README.md) | [中文](README.zh-CN.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/piia-engram)](https://pypi.org/project/piia-engram/)

</div>

---

> **TL;DR:** Engram is an MCP server that gives Claude Code, Codex, and Cursor a persistent identity layer — your profile, preferences, lessons learned, and key decisions stored as local JSON files. One write, every AI reads. 100% local, Apache 2.0.

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

Unlike session-memory tools that remember what happened in a task, Engram stores **who you are** — your identity, preferences, lessons, and decisions — so every AI tool starts from the same understanding of you as a person.

## Who Uses Engram

Engram fits anyone whose work involves accumulated judgment — not one-time tasks, but years of lessons, decisions, and hard-won standards that an AI tool should already know.

**Developers**  
Your quality standards (test coverage, naming rules, which hacks you refuse to ship), architecture decisions, and hard-won lessons exist only in your head — and reset every session. With Engram, day one of a new project is not day one: your AI already knows your bottom line.

**Investment analysts**  
Decisions get made but reasoning gets lost. Engram stores the full reasoning chain so six months later, "why did I pass on that?" has a real answer — and your analytical framework travels with you across every new analysis.

**System architects**  
Architecture decisions need context: what you chose, what you ruled out, and why. Engram keeps living Architecture Decision Records that travel with you across companies and projects, queryable by any AI tool.

**Backend developers**  
API quirks, integration gotchas, performance trade-offs — tacit knowledge that normally lives in your head and resets when you change jobs. Engram turns it into a searchable library that persists across everything.

**Frontend and design**  
Design philosophy rarely gets documented in a way AI tools can use. Engram stores your real standards, UX lessons from real users, and the reasoning behind component decisions — so every project starts where your last one ended.

**Vibe coders**  
You build with AI and move fast. The problem: every new session your AI starts from scratch — different style choices, inconsistent patterns, re-explaining the same preferences. Engram makes every tool consistent from session one: your stack, your patterns, your voice, already there.

## What Engram Stores

All data lives under `~/.engram/` as plain JSON and Markdown files you can open, edit, back up, or migrate yourself.

- **Identity**: who you are, how you communicate, what languages you prefer
- **Quality standards**: your code review bar, test coverage expectations, what you refuse to ship
- **Preferences**: coding style, AI behavior, how you like explanations
- **Trust boundaries**: which fields to keep private, what tools can access
- **Project snapshots**: context for ongoing work, captured and reloadable
- **Lessons learned**: mistakes, surprises, things that worked and didn't
- **Key decisions**: what you chose, what you ruled out, and why
- **Domain knowledge**: reusable insights across projects and tools

## What Engram Does (Beyond Storage)

Most memory tools are passive — you put things in, they give them back. Engram is also active.

**Knowledge inheritance across projects**  
Describe a new project in plain text. `get_knowledge_inheritance` returns a curated starter pack of the most relevant lessons and decisions from everything you have ever worked on. Your tenth project benefits from all nine before it — automatically.

**Passive knowledge capture**  
Paste a session summary into `extract_session_insights` and Engram automatically extracts and stores the lessons and decisions. No manual note-taking. Knowledge accumulates even when you are not thinking about it.

**Works with tools that do not support MCP**  
ChatGPT, Gemini, Kimi — `get_identity_card` exports a ready-to-paste Markdown identity card. Your context travels even to tools that cannot connect directly.

**Knowledge health and discovery**  
`get_knowledge_overview` surfaces stale lessons (not reviewed in 90+ days), gives a health score, and flags gaps worth revisiting. `find_similar_knowledge` finds overlapping items to merge. `link_knowledge` connects related lessons and decisions into a navigable knowledge graph.

## Local Setup

Run Engram on your own machine. Data stays in `~/.engram/`, AI tools connect over stdio.

```bash
git clone https://github.com/Patdolitse/engram.git
cd engram
pip install piia-engram      # Install from PyPI (recommended)
# Or install from source: pip install -e .
python demos/setup_engram.py
```

Add to your AI tool's MCP config:

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

Restart your MCP-compatible client. A new session will call `get_user_context` automatically.

## Upgrading

```bash
pip install --upgrade piia-engram
```

After upgrading, Engram automatically migrates any stale MCP configs the next time its server starts (stdio mode). If your AI tool still shows an "MCP disconnected" error after restarting, run:

```bash
engram doctor        # show what's wrong
engram doctor --fix  # auto-repair and fix in one step
```

Then restart the affected AI tool. The doctor command checks Claude Code, Cursor, and Claude Desktop configs and removes any outdated server entries.

## Remote Deployment

Run Engram on your own server and connect from anywhere.

### Server Setup

```bash
# Install with remote support
pip install piia-engram[remote]

# Generate an auth token
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Save the output, e.g. "abc123..."

# Start in SSE mode
ENGRAM_AUTH_TOKEN=abc123... python -m engram_core.mcp_server --transport sse --host 0.0.0.0 --port 8767
```

### Client Config (Claude Code)

```json
{
  "mcpServers": {
    "engram": {
      "url": "http://your-server:8767/sse",
      "headers": {
        "Authorization": "Bearer abc123..."
      }
    }
  }
}
```

### Client Config (Cursor)

```json
{
  "mcpServers": {
    "engram": {
      "url": "http://your-server:8767/sse",
      "headers": {
        "Authorization": "Bearer abc123..."
      }
    }
  }
}
```

**Security notes:**
- Always use HTTPS in production, behind nginx or caddy with TLS.
- The auth token protects your identity data. Keep it secret.
- Default bind is `127.0.0.1` for localhost only. Use `0.0.0.0` only behind a reverse proxy.
- Data stays on your server and never touches third-party clouds.

## MCP Tools

Engram exposes read, write, project, backup, and compatibility tools through MCP.

Common tools include:

| Tool | Purpose |
|---|---|
| `get_user_context` | Load the complete user context at the start of a session |
| `get_identity_card` | Export a Markdown identity card for tools without MCP |
| `get_profile` | Read the user profile, optionally filtered with `safe=true` |
| `get_preferences` | Read communication and workflow preferences |
| `get_trust_boundaries` | Read data access boundaries |
| `get_quality_standards` | Read quality expectations |
| `get_lessons` | Read reusable lessons learned |
| `get_decisions` | Read key decisions and reasons |
| `get_relevant_knowledge` | Find knowledge relevant to a project |
| `get_knowledge_inheritance` | Build a cross-project knowledge starter pack from free text |
| `save_project_snapshot` | Save project context for later sessions |
| `add_lesson` | Add a lesson learned |
| `add_decision` | Add a key decision |
| `bulk_add_knowledge` | Add multiple lessons or decisions in one call |
| `ingest_notes` | Parse free-form notes into lessons and decisions |
| `extract_session_insights` | Extract lessons and decisions from session summaries |
| `export_engram` | Export a full backup |
| `import_engram` | Import a backup |
| `export_engram_to_openclaw` | Export OpenClaw-compatible files |
| `import_engram_from_openclaw` | Import OpenClaw-compatible files |
| `search_knowledge` | Search lessons and decisions by weighted multi-term relevance |
| `get_knowledge_overview` | Knowledge overview: digest, health report, and stale checks |
| `get_related_knowledge` | Follow links between lessons and decisions |
| `find_similar_knowledge` | Find similar lessons and decisions by content |
| `export_knowledge_report` | Export a readable Markdown knowledge report |
| `link_knowledge` | Create a bidirectional link between two knowledge items |
| `unlink_knowledge` | Remove a bidirectional knowledge link |
| `merge_knowledge` | Merge a duplicate knowledge item into the primary item |
| `update_knowledge` | Update a lesson or decision by ID |
| `archive_knowledge` | Archive a lesson or decision by ID |
| `get_audit_log` | Get recent audit log entries |

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

## FAQ

**What is Engram?**
Engram is a local-first MCP server that gives AI coding tools (Claude Code, Codex, Cursor) a persistent identity layer. It stores who you are, how you work, what you have learned, and the decisions you have made — as local JSON files on your machine.

**How is Engram different from other AI memory tools?**
Most AI memory tools store what happened in a session (task context, code changes). Engram stores who you are as a person — your identity, preferences, lessons, and decisions. This identity layer persists across tools, sessions, and projects. Your data is local JSON files you own and can edit directly.

**Which AI tools does Engram support?**
Engram works with any MCP-compatible AI tool: Claude Code, OpenAI Codex, Cursor, Claude Desktop, and others. For tools without MCP support (ChatGPT, Gemini, Kimi), you can export a Markdown identity card and paste it in manually.

**How do I install Engram?**
```bash
git clone https://github.com/Patdolitse/engram.git
pip install piia-engram
# Or from source: cd engram && pip install -e .
python demos/setup_engram.py
```
Then add the MCP config and restart your AI tool. The AI will call `get_user_context` automatically at the start of each session.

**After upgrading, my AI tool shows "MCP server disconnected". How do I fix it?**
Run `engram doctor --fix` in a terminal, then restart your AI tool. This command scans all known MCP config files (Claude Code, Cursor, Claude Desktop), removes outdated server entries, and repairs broken paths in one step. Engram also runs this migration automatically the next time its server starts, so most users will never see this message.

**Does Engram send data to the cloud?**
All data is stored in `~/.engram/` on your local machine. Engram itself never uploads data anywhere. The optional `read_web_content` tool makes outbound HTTP requests to a local Reader service (`localhost:7890`) which may in turn fetch external URLs — but only when explicitly invoked. Core identity and knowledge tools make no network requests.

**How many MCP tools does Engram provide?**
Engram exposes 37 MCP tools covering identity management, lessons learned, key decisions, project snapshots, bulk input, note ingestion, session insight extraction, weighted knowledge search, similarity discovery, merging, digesting, reporting, linking, health checks, and audit logging.

**Is Engram free?**
Yes. Engram is free and open source under the Apache 2.0 license.

## Limitations

Engram is functional and actively used, but some things it intentionally does not do yet:

| Area | Current State | Planned |
|---|---|---|
| **File safety** | Atomic JSON writes with a shared portalocker file lock | Broader stress testing |
| **Access control** | `restricted_fields` filters profile fields from `get_user_context` and `get_profile(safe=true)` | Per-caller ACL blocked by MCP caller identity |
| **Encryption** | Optional field-level AES-256-GCM encryption via `ENGRAM_SECRET` env var. Install `pip install piia-engram[secure]`. | Full-disk encryption for all files (v4.0) |
| **Audit logging** | Optional access audit log via `ENGRAM_AUDIT=1` env var. Logs to `~/.engram/audit.log`. | Per-caller audit (blocked by MCP spec) |
| **Caller identity** | MCP protocol doesn't pass tool identity | Blocked by MCP spec |
| **Concurrent writes** | Protected by file lock + atomic replace for Engram JSON writes | Network-filesystem edge cases not guaranteed |

**What this means in practice:**
- Don't store passwords, API keys, or client PII in Engram
- Any process with read access to `~/.engram/` can read your data
- `restricted_fields` reduces what Engram emits in cold-start context, but it is not encryption or a true ACL

This is not a warning to avoid Engram — it's an honest description of what it is: a local memory layer for personal AI context. For personal use, it works well today.

## Security Configuration

### Field-level encryption (optional)

Encrypt sensitive profile fields (email, phone, location, etc.) at rest:

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="your-strong-passphrase"
```

Encrypted fields are stored as `enc:v1:...` in JSON files. Without `ENGRAM_SECRET`, Engram works normally with plaintext (backward compatible).

### Audit logging (optional)

Track all read/write operations:

```bash
export ENGRAM_AUDIT=1
```

Logs are written to `~/.engram/audit.log` in JSON-lines format. Query with `get_audit_log` tool or `grep`.

## Contributing

Contributions, issues, and feedback are welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md). Chinese readers can also use [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md).

## License

[Apache 2.0](LICENSE). Engram is free software. Your memory belongs to you.
