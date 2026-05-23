<!-- mcp-name: io.github.Patdolitse/piia-engram -->
<div align="center">

<img src="assets/social_preview.png" alt="piia-engram — persistent AI memory across tools" width="640">

# piia-engram

### Remember who you are across every AI tool

**Your preferences, code standards, lessons learned, and decisions — persistent across Claude Code, Cursor, Codex, and any MCP tool. Local-first, zero-cloud.**

`persistent memory` | `cross-tool context` | `Claude Code` | `Codex` | `Cursor` | `Windsurf` | `MCP` | `local-first`

[ENGLISH](README.md) | [中文](README.zh-CN.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/piia-engram)](https://pypi.org/project/piia-engram/)
[![Downloads](https://img.shields.io/pypi/dm/piia-engram)](https://pypi.org/project/piia-engram/)
[![Official MCP Registry](https://img.shields.io/badge/MCP_Registry-official-green.svg)](https://registry.modelcontextprotocol.io)

</div>

---

> **TL;DR:** piia-engram stores your identity, preferences, lessons learned, and key decisions as local JSON files — and shares them with every AI tool through MCP. Set up once, every AI tool remembers you. No cloud, no lock-in, Apache 2.0.

---

**Your AI forgets you every time you switch tools or start a new chat.** piia-engram fixes that.

Every time you open a new chat window, switch from Claude Code to Codex, update your AI tool, or move into a different project, you're back to zero:

- your communication preferences — gone
- your code standards and quality bar — forgotten
- which mistakes you've already learned from — lost
- why you made that architecture decision last month — erased

This happens because AI memory today is locked inside each platform. It belongs to the tool, not to you. The tool updates, resets, or gets replaced — and your context disappears with it.

**piia-engram gives you persistent memory that lives on your machine, independent of any AI tool.** You tell it once who you are, how you work, and what you've learned. Every MCP-compatible tool reads the same context. New chat, new tool, new version — your identity persists.

> **piia-engram is not an agent memory database.** Tools like Mem0, Zep, and Letta store task context and session history for AI agents. piia-engram stores *who you are as a person* — your identity, preferences, hard-won lessons, and key decisions. It's a different layer: not what happened in a task, but who is behind every task.

## Why piia-engram?

| Without piia-engram | With piia-engram |
|---|---|
| New chat window = start from zero | Every conversation already knows you |
| AI tool updates and your preferences vanish | Your identity lives on your machine, survives any update |
| Switching tools loses accumulated context | Claude Code, Codex, and Cursor read the same memory |
| Past mistakes get repeated | Lessons learned follow you across tools and sessions |
| Memory is locked inside one product | Data stays local, editable, and portable |

## Who Uses piia-engram

piia-engram is built for developers who use multiple AI coding tools and are tired of re-explaining themselves.

**If you switch between Claude Code, Codex, and Cursor** — your code standards, architecture decisions, and hard-won lessons reset every time. piia-engram makes every tool start from the same understanding of who you are.

**If you open 10+ AI chat windows a week** — each one starts from zero. piia-engram gives every conversation your full context from the first message.

**If you've lost preferences after a tool update** — your identity lives on your machine, not inside any platform. Updates, resets, and migrations don't touch your memory.

<details>
<summary><strong>Other use cases</strong></summary>

**Investment analysts**
Decisions get made but reasoning gets lost. piia-engram stores the full reasoning chain so six months later, "why did I pass on that?" has a real answer — and your analytical framework travels with you across every new analysis.

**System architects**
Architecture decisions need context: what you chose, what you ruled out, and why. piia-engram keeps living Architecture Decision Records that travel with you across companies and projects, queryable by any AI tool.

**Backend developers**
API quirks, integration gotchas, performance trade-offs — tacit knowledge that normally lives in your head and resets when you change jobs. piia-engram turns it into a searchable library that persists across everything.

**Frontend and design**
Design philosophy rarely gets documented in a way AI tools can use. piia-engram stores your real standards, UX lessons from real users, and the reasoning behind component decisions — so every project starts where your last one ended.

**Vibe coders**
You build with AI and move fast. The problem: every new session your AI starts from scratch — different style choices, inconsistent patterns, re-explaining the same preferences. piia-engram makes every tool consistent from session one: your stack, your patterns, your voice, already there.

</details>

## What piia-engram Stores

All data lives under `~/.engram/` as plain JSON and Markdown files you can open, edit, back up, or migrate yourself.

- **Identity**: who you are, how you communicate, what languages you prefer
- **Quality standards**: your code review bar, test coverage expectations, what you refuse to ship
- **Preferences**: coding style, AI behavior, how you like explanations
- **Trust boundaries**: which fields to keep private, what tools can access
- **Project snapshots**: context for ongoing work, captured and reloadable
- **Lessons learned**: mistakes, surprises, things that worked and didn't
- **Key decisions**: what you chose, what you ruled out, and why
- **Domain knowledge**: reusable insights across projects and tools

## What piia-engram Does (Beyond Storage)

Most memory tools are passive — you put things in, they give them back. piia-engram is also active.

**Knowledge inheritance across projects**  
Describe a new project in plain text. `get_knowledge_inheritance` returns a curated starter pack of the most relevant lessons and decisions from everything you have ever worked on. Your tenth project benefits from all nine before it — one tool call away.

**Passive knowledge capture**  
Paste a session summary into `extract_session_insights` and piia-engram extracts and stores the lessons and decisions. No manual note-taking. Knowledge accumulates through normal AI conversations.

**Works with tools that do not support MCP**  
ChatGPT, Gemini, Kimi — `get_identity_card` exports a ready-to-paste Markdown identity card. Your context travels even to tools that cannot connect directly.

**Knowledge health and discovery**  
`get_knowledge_overview` surfaces stale lessons (not reviewed in 30+ days), computes a 0–100 health score across four dimensions (freshness, quality, coverage, cleanliness), and flags gaps worth revisiting. `suggest_merges` scans your entire knowledge base for near-duplicates and returns actionable merge commands. `link_knowledge` connects related lessons and decisions into a navigable knowledge graph.

## Quick Start (30 seconds)

```bash
pip install piia-engram
engram setup
```

The setup wizard will:
1. Detect your Python environment
2. Find and configure your AI tools (Claude Code, Cursor, Claude Desktop)
3. Walk you through seed knowledge (role, tech stack, language)
4. Smart-import rules from your existing `CLAUDE.md` / `.cursorrules` files
5. Show your privacy preferences (cross-tool sync, anonymous statistics — both optional)
6. **Preview your AI identity card** — immediate proof of value

Restart your AI tool after setup. The first conversation will call `get_user_context` automatically — your AI already knows you.

### Configure for Your AI Tool

<details open>
<summary><strong>Claude Code</strong></summary>

```bash
# Automatic (recommended)
engram setup
# Or manual:
claude mcp add piia-engram -- python -m piia_engram.mcp_server
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "piia-engram": {
      "command": "python",
      "args": ["-m", "piia_engram.mcp_server"]
    }
  }
}
```

</details>

<details>
<summary><strong>Codex (OpenAI)</strong></summary>

Add to `~/.codex/mcp.json`:
```json
{
  "mcpServers": {
    "piia-engram": {
      "command": "python",
      "args": ["-m", "piia_engram.mcp_server"]
    }
  }
}
```

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "piia-engram": {
      "command": "python",
      "args": ["-m", "piia_engram.mcp_server"]
    }
  }
}
```

</details>

<details>
<summary><strong>Windsurf / Copilot / Cline / Other MCP clients</strong></summary>

Any tool that supports MCP over stdio works. Use this config:
```json
{
  "mcpServers": {
    "piia-engram": {
      "command": "python",
      "args": ["-m", "piia_engram.mcp_server"]
    }
  }
}
```

For tools without MCP support (ChatGPT, Gemini, Kimi): run `get_identity_card` in any MCP tool and paste the exported Markdown card into your chat.

</details>

### See It in Action

```
You  → "Help me refactor this auth module"

# WITHOUT piia-engram: AI starts from scratch
AI   → "What language? What framework? What's your testing preference?"

# WITH piia-engram: AI already knows you
AI   → "Based on your preference for pytest + 90% coverage, and your
        lesson about always separating auth middleware from business
        logic (from the March incident), here's my approach..."
```

After setup, run `engram doctor` to verify everything is connected:

```
$ engram doctor

  Detected 3 AI tool(s):
    [ok] Claude Code — Engram configured
    [ok] Cursor — Engram configured
    [ok] Codex — Engram configured

  [ok] All configured tools look healthy.

  ── Functional Checks ──
    [ok] piia_engram.core importable
    [ok] Engram initialized (~/.engram)
    [ok] Identity loaded (role: Senior Backend Developer)
    [ok] quick_context.md ready (4096 bytes)
    [ok] MCP server: 14 tools registered
```


## Upgrading

```bash
pip install --upgrade piia-engram
```

After upgrading, piia-engram automatically migrates any stale MCP configs the next time its server starts (stdio mode). If your AI tool still shows an "MCP disconnected" error after restarting, run:

```bash
piia-engram doctor        # show what's wrong
piia-engram doctor --fix  # auto-repair and fix in one step
```

Then restart the affected AI tool. The doctor command checks Claude Code, Cursor, and Claude Desktop configs and removes any outdated server entries.

## Remote Deployment

Run piia-engram on your own server and connect from anywhere.

### Server Setup

```bash
# Install with remote support
pip install piia-engram[remote]

# Generate an auth token
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Save the output, e.g. "abc123..."

# Start in SSE mode
ENGRAM_AUTH_TOKEN=abc123... python -m piia_engram.mcp_server --transport sse --host 0.0.0.0 --port 8767
```

### Client Config (Claude Code)

```json
{
  "mcpServers": {
    "piia-engram": {
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
    "piia-engram": {
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
- Set `ENGRAM_CORS_ORIGINS` to restrict cross-origin access (e.g. `https://your-domain.com`).
- Data stays on your server and never touches third-party clouds.

## MCP Tools

piia-engram ships 51 MCP tools. By default, only the 14 **Tier-1 Core** tools are loaded to keep the AI's context clean. To unlock all 51 tools, add `ENGRAM_TOOLS=all` to your MCP config:

```json
{
  "mcpServers": {
    "piia-engram": {
      "command": "python",
      "args": ["-m", "piia_engram.mcp_server"],
      "env": { "ENGRAM_TOOLS": "all" }
    }
  }
}
```

### Tier-1 Core (14 tools — daily workflow)

| Tool | Purpose |
|---|---|
| `get_user_context` | Load identity + knowledge at session start |
| `wrap_up_session` | Save insights + sync at session end |
| `add_lesson` | Store a reusable lesson learned |
| `add_decision` | Record a key decision with reasoning |
| `add_playbook` | Record an operational playbook (multi-step procedure with trigger keywords) |
| `search_knowledge` | Search lessons, decisions, and playbooks by weighted relevance |
| `get_relevant_knowledge` | Find knowledge relevant to current project |
| `get_identity_card` | Export Markdown identity card for non-MCP tools |
| `update_identity` | Update profile, preferences, or quality standards |
| `get_project_context` | Read a saved project snapshot |
| `save_project_snapshot` | Persist project state for future sessions |
| `save_agent_context` | Auto-save AI session checkpoint for crash recovery |
| `get_recent_context` | Recover lost session context after restart |
| `list_agent_sessions` | Browse saved session records across tools |

### Tier-2 Advanced (37 tools — knowledge management, review, import/export)

<details>
<summary>Click to expand full tool list</summary>

| Tool | Purpose |
|---|---|
| `refresh_quick_context` | Refresh local `quick_context.md` snapshot for offline/cross-tool use |
| `get_profile` | Read user profile (safe=true by default) |
| `get_work_style` | Read work style preferences |
| `get_preferences` | Read communication and workflow preferences |
| `get_trust_boundaries` | Read data access boundaries |
| `get_quality_standards` | Read quality expectations |
| `get_playbooks` | List saved operational playbooks |
| `get_playbook` | Get full content of a single playbook by ID |
| `get_lessons` | List reusable lessons learned |
| `get_decisions` | List key decisions and reasons |
| `get_domains` | Read domain experience stats |
| `get_knowledge_inheritance` | Build cross-project knowledge starter pack |
| `list_projects` | List saved project snapshots |
| `extract_session_insights` | Extract lessons and decisions from session text |
| `bulk_add_knowledge` | Add multiple lessons or decisions in one call |
| `ingest_notes` | Parse free-form notes into structured knowledge |
| `update_knowledge` | Update a lesson or decision by ID |
| `archive_knowledge` | Archive a lesson or decision by ID |
| `review_knowledge` | Mark a knowledge item as reviewed |
| `merge_knowledge` | Merge a duplicate into the primary item |
| `link_knowledge` | Create a bidirectional link between items |
| `unlink_knowledge` | Remove a bidirectional knowledge link |
| `get_knowledge_overview` | Knowledge digest, health report, stale checks |
| `get_related_knowledge` | Follow links between knowledge items |
| `find_similar_knowledge` | Find similar items by content |
| `suggest_merges` | Scan for near-duplicates with actionable merge commands |
| `get_stale_knowledge` | List items that need review |
| `export_knowledge_report` | Export a readable Markdown knowledge report |
| `request_outline_review` | Generate an interactive HTML review page |
| `apply_review` | Process review results (promote/archive staging items) |
| `export_engram` | Export a full backup |
| `import_engram` | Import a backup |
| `export_engram_to_openclaw` | Export OpenClaw-compatible files |
| `import_engram_from_openclaw` | Import OpenClaw-compatible files |
| `read_web_content` | Read webpage via local Reader service |
| `get_audit_log` | Get recent audit log entries |
| `start_project` | Start a project with inherited knowledge |

</details>

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
|-- contexts/
|   `-- {tool_name}/
|       `-- {session_id}.md
|-- exports/
`-- compat/
    `-- openclaw/
```

## Supported Tools

| Tool | Integration | Confidence |
|---|---|---|
| Claude Code | MCP over stdio | ✅ Verified |
| Codex | MCP over stdio | ✅ Verified |
| Cursor | MCP over stdio | ✅ Verified |
| Claude Desktop | MCP over stdio | ✅ Verified |
| Windsurf | MCP over stdio | Expected to work |
| GitHub Copilot | MCP over stdio | Expected to work |
| Cline | MCP over stdio | Expected to work |
| Roo Code | MCP over stdio | Expected to work |
| Amazon Q | MCP over stdio | Expected to work |
| Augment | MCP over stdio | Expected to work |
| Zed | MCP over stdio | Expected to work |
| OpenClaw | SOUL.md / MEMORY.md / USER.md import and export | ✅ Verified |
| ChatGPT / Gemini / Kimi | Markdown identity card fallback | 🔧 Usable |

## Comparison

| Feature | piia-engram | Claude Memory | Manual `CLAUDE.md` | Mem0 | Letta (MemGPT) |
|---|---|---|---|---|---|
| Primary purpose | User identity across tools | Per-conversation memory | Per-project notes | Agent vector memory | Agent self-editing memory |
| Cross-tool by design | ✅ MCP-native (48 tools) | ❌ Claude only | ❌ tool-specific | ⚠ requires per-tool wiring | ⚠ requires per-tool wiring |
| Storage | Local JSON in `~/.engram/` | Cloud | Local | Vector DB + Mem0 Cloud | Postgres or Letta Cloud |
| Local-first by default | ✅ | ❌ | ✅ | ⚠ Cloud is the default | ⚠ Cloud is the default |
| Encryption at rest | ✅ AES-256-GCM, PBKDF2 600k (opt-in) | depends on Cloud | ❌ plain Markdown | depends on store config | depends on Postgres config |
| Knowledge tiers (user gate) | ✅ staging → verified | ❌ | ❌ | ❌ | ❌ |
| Conflict detection | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP-native | ✅ | n/a | n/a | ⚠ third-party | ⚠ third-party |
| Price | Free, Apache 2.0 | Subscription-bundled | Free | Free / Cloud tiers | Free / Cloud tiers |

📊 **For the full side-by-side**, including when to choose a competitor over piia-engram, see [`docs/comparison.md`](docs/comparison.md).

## By the numbers

These are factual claims about piia-engram itself, refreshed each minor release.

| | v3.24.0 (2026-05-23) |
|---|---|
| Supported AI tools | **13** (4 verified + 7 expected-to-work + OpenClaw + ChatGPT fallback) |
| MCP tools exposed | **51** (14 Tier-1 default, 37 opt-in via `ENGRAM_TOOLS=all`) |
| Knowledge types | **3** (lessons, decisions, playbooks) |
| Tests passing | **721** (unit + integration) |
| Code coverage | **96%** total; mcp_server 99%, setup_wizard 93%, storage 100%, core 95% |
| Lines in `core.py` | **1097** (down from 4277 pre-v3.14.1 — see [architecture.md](docs/architecture.md)) |
| PBKDF2 iterations | **600,000** (OWASP 2023+ floor; legacy 100k still decrypts) |
| Encryption | AES-256-GCM, per-value random salt + nonce |
| Cold-start time | < 100 ms typical (local JSON, no network) |
| Network calls from core | **0** by default — except optional `read_web_content` and opt-in anonymous usage statistics (Phase 2: local + optional remote — [details](docs/telemetry_roadmap.md)) |
| External AI evaluations | 4 independent AIs evaluated the telemetry design; 3 earlier evaluations on architecture (see [`docs/`](docs/)) |

## Built With

piia-engram is a human-directed, AI-assisted open-source project.

| Contributor | Role |
|---|---|
| [@Patdolitse](https://github.com/Patdolitse) | Creator, product direction, strategy, ownership |
| Claude Code | Architecture, task planning, code review assistance |
| Codex | Implementation, testing, documentation assistance |

## FAQ

**What MCP server lets me share memory between Claude Code and Cursor?**
piia-engram. Install with `pip install piia-engram && engram setup`, and both tools read the same identity, preferences, and lessons from `~/.engram/`. No cloud, no sync service — they both read local JSON files through MCP.

**What is piia-engram?**
piia-engram is a persistent memory layer for AI tools. It stores your identity, preferences, code standards, lessons learned, and key decisions as local JSON files on your machine. Every MCP-compatible AI tool (Claude Code, Codex, Cursor, Windsurf, Claude Desktop) reads the same context, so new chats, tool updates, and tool switches never erase who you are.

**How is piia-engram different from the official MCP memory server?**
The official `@modelcontextprotocol/server-memory` stores a generic knowledge graph of entities and relations. piia-engram is specialized for **developer identity**: it has structured fields for your profile, code standards, quality bar, lessons learned, and key decisions — plus 48 tools for knowledge lifecycle management (search, review, merge, inherit across projects). If you need general-purpose entity memory, use the official server. If you want every AI tool to know your coding preferences and past mistakes, use piia-engram.

**How is piia-engram different from agent memory tools like Mem0, Zep, or Letta?**
Those tools store task context and session history for AI agents — what happened during a workflow. piia-engram stores who *you* are as a person — your identity, preferences, hard-won lessons, and key decisions. It's a different layer: identity persists across tools, sessions, and projects, while task memory is scoped to a single agent run. Your data is local JSON files you own and can edit directly.

**Can I use piia-engram with multiple AI tools at once?**
Yes. That's the primary use case. piia-engram uses local file storage (`~/.engram/`) with atomic writes and file locking. Claude Code, Cursor, Codex, and any other MCP client can connect simultaneously. A lesson recorded in Claude Code is immediately available in Cursor.

**Which AI tools does piia-engram support?**
Any MCP-compatible tool: Claude Code, OpenAI Codex, Cursor, Claude Desktop, Windsurf, GitHub Copilot, Cline, Roo Code, Amazon Q, Augment, Zed, and more. For tools without MCP support (ChatGPT, Gemini, Kimi), export a Markdown identity card with `get_identity_card` and paste it in.

**Where is my data stored?**
All data lives in `~/.engram/` on your local machine as plain JSON and Markdown files. No cloud, no account, no subscription. You can open, edit, back up, or migrate the files yourself. Optional AES-256-GCM encryption is available via `pip install piia-engram[secure]`.

**How do I install piia-engram?**
```bash
pip install piia-engram
engram setup
```
The setup wizard detects your AI tools and configures MCP automatically. Restart your AI tool after setup. The AI will call `get_user_context` at the start of each session.

**After upgrading, my AI tool shows "MCP server disconnected". How do I fix it?**
Run `engram doctor --fix` in a terminal, then restart your AI tool. This command scans all known MCP config files, removes outdated server entries, and repairs broken paths in one step.

**Does piia-engram send data to the cloud?**
No. All core tools make zero network requests. Optional anonymous usage statistics (tool call counts, never content) can be enabled during setup but are **off by default**. You can inspect the payload with `engram telemetry preview` and disable anytime with `engram telemetry off`.

**How many MCP tools does piia-engram provide?**
48 tools: 13 Tier-1 Core tools loaded by default (identity, knowledge, project context, session recovery) plus 35 Tier-2 Advanced tools for knowledge management, review, import/export, and audit logging. Enable all with `ENGRAM_TOOLS=all`.

**Is piia-engram free?**
Yes. Free and open source under the Apache 2.0 license. No subscription, no cloud tiers, no vendor lock-in.

## Limitations

piia-engram is functional and actively used, but some things it intentionally does not do yet:

| Area | Current State | Planned |
|---|---|---|
| **File safety** | Atomic JSON writes with a shared portalocker file lock | Broader stress testing |
| **Access control** | `restricted_fields` filters profile in `get_user_context`, `get_profile` (default safe=true), `get_identity_card`, and resource endpoints | Per-caller ACL blocked by MCP caller identity |
| **Encryption** | Optional field-level AES-256-GCM encryption via `ENGRAM_SECRET` env var. Install `pip install piia-engram[secure]`. | Full-disk encryption for all files (v4.0) |
| **Audit logging** | Optional access audit log via `ENGRAM_AUDIT=1` env var. Logs to `~/.engram/audit.log`. | Per-caller audit (blocked by MCP spec) |
| **Caller identity** | MCP protocol doesn't pass tool identity | Blocked by MCP spec |
| **Concurrent writes** | Protected by file lock + atomic replace for piia-engram JSON writes | Network-filesystem edge cases not guaranteed |

**What this means in practice:**
- Don't store passwords, API keys, or client PII in piia-engram
- Any process with read access to `~/.engram/` can read your data
- `restricted_fields` reduces what piia-engram emits in cold-start context, but it is not encryption or a true ACL

This is not a warning to avoid piia-engram — it's an honest description of what it is: a local memory layer for personal AI context. For personal use, it works well today.

## Security Configuration

### Field-level encryption (optional)

Encrypt sensitive profile fields (email, phone, location, etc.) at rest:

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="your-strong-passphrase"
```

Encrypted fields are stored as `enc:v1:...` in JSON files. Without `ENGRAM_SECRET`, piia-engram works normally with plaintext (backward compatible).

### Audit logging (optional)

Track all read/write operations:

```bash
export ENGRAM_AUDIT=1
```

Logs are written to `~/.engram/audit.log` in JSON-lines format. Query with `get_audit_log` tool or `grep`.

## CLI Commands

```bash
engram setup            # Interactive install wizard
piia-engram doctor           # Check config health (all AI tools)
piia-engram doctor --fix     # Auto-repair any issues found
piia-engram stats            # Show project growth metrics (GitHub + PyPI)
piia-engram stats --log      # Append stats snapshot to local log
engram telemetry        # Manage anonymous usage statistics
engram privacy          # Show what data piia-engram stores and where
```

## Contributing

Contributions, issues, and feedback are welcome.

See [CONTRIBUTING.md](CONTRIBUTING.md). Chinese readers can also use [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md).

## License

[Apache 2.0](LICENSE). piia-engram is free software. Your memory belongs to you.
