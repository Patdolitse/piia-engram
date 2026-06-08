---
name: engram
description: Local-first personal AI identity and memory layer for MCP-compatible coding tools (Claude Code, Codex, Cursor, and others). Use this skill when the user wants to continue from a previous session ("continue from last session", "pick up where we left off"), recall a past decision ("what did we decide", "what was our reasoning"), persist something durable ("remember this", "save a lesson", "save a decision", "save a playbook"), search prior knowledge ("search what we know about X", "have we hit this before"), export their identity or context ("export my identity card", "give me my context"), or maintain local-first cross-tool identity and memory that the user owns and approves. Engram stores user-approved lessons, decisions, playbooks, and project context as local JSON; the AI suggests, the user decides what becomes permanent.
license: AGPL-3.0-or-later
---

# Engram

Engram is a local-first personal AI identity and memory layer exposed over MCP.
It lets MCP-compatible coding tools (Claude Code, Codex, Cursor, and other MCP
clients) start from the same user-approved understanding of who the user is, what
they've decided, and what they've learned — without a cloud account and without
hidden memory the user cannot inspect.

This skill tells you **when** to reach for Engram and **which existing MCP tools**
to use. It does not add new behavior; it routes to the Engram MCP server.

## When to use this skill

Reach for Engram when the user's request implies continuity, recall, or durable
memory rather than a one-off task:

| Signal | Example phrasing | Where to start |
| --- | --- | --- |
| Resume work | "continue from last session", "pick up where we left off" | `get_resume_brief` |
| Recall a decision | "what did we decide", "why did we choose X" | `search_knowledge`, `get_relevant_knowledge` |
| Save a lesson | "remember this", "save a lesson", "note this gotcha" | `add_lesson` |
| Save a decision | "record this decision", "we chose X because Y" | `add_decision` |
| Save a playbook | "save this as a playbook", "remember these steps" | `add_playbook` |
| Search prior knowledge | "have we seen this before", "search what we know about X" | `search_knowledge` |
| Identity / preferences | "who am I to you", "what are my preferences" | `get_user_context`, `get_identity_card` |
| Export identity/context | "export my identity card", "give me my context" | `get_identity_card` |
| End of session | wrapping up, summarizing what changed | `wrap_up_session` |

When the request is a normal coding task with no continuity or memory angle, do
**not** invoke Engram — just do the task.

## How to use it (routing, not magic)

1. **Start of a continued session** — call `get_resume_brief` to recover the last
   thread of work. For identity and preferences on a fresh project, call
   `get_user_context`.
2. **During work** — when the user asks what was decided or learned, call
   `search_knowledge` (topic known) or `get_relevant_knowledge` (let Engram pick
   what's relevant). Normal read/search tools provide session context; export
   surfaces such as `get_identity_card` are owner-gated and can write local
   files.
3. **Capturing durable knowledge** — the user, not the AI, owns what becomes
   permanent. When the user says to remember something, propose it and write it
   with `add_lesson` / `add_decision` / `add_playbook`. These are
   user-approved writes, not automatic background memory.
4. **End of session** — call `wrap_up_session` to checkpoint context so the next
   tool (or the next session) can resume.

Some MCP clients also run session hooks that capture context automatically; that
context lands in the **user-visible daily log and the staging tier**, where it is
inspectable and is **not** silently promoted to verified/trusted knowledge.

The full read/write tool map is in [references/tools.md](references/tools.md).
Privacy, ownership, and storage boundaries are in
[references/privacy.md](references/privacy.md).

## Honest boundaries

- Engram **suggests**; the user **decides**. AI-suggested knowledge is **staged
  for review**, not silently promoted to verified/trusted memory; everything
  written lands in the user's local store where it can be inspected.
- Storage is **local JSON the user owns**. There is no cloud account and no
  vendor lock-in. Telemetry is **off by default**; if enabled it writes a local
  log only, and any remote sending is a separate explicit opt-in.
- Knowledge moves through a **staging → verified** path so unreviewed entries do
  not silently become trusted facts.
- Do **not** claim capabilities Engram does not have. Use only the tool names in
  [references/tools.md](references/tools.md); do not invent tools.

## MCP server

Engram runs as an MCP server via the `piia-engram-mcp` command. Configure your
MCP client to launch it (the Cursor plugin skeleton under `.cursor-plugin/`
shows one such wiring). By default the server exposes a Tier-1 core tool set;
the full set is available with `ENGRAM_TOOLS=all`.
