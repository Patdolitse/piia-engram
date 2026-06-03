# Engram Cross-Tool & Cross-Session Usage Guide

> Version: 3.46.0+ | Updated: 2026-06-03

This guide is for users who work with multiple AI tools at the same time (Claude Code, Codex, Cursor, etc.). It explains how to keep Engram's memory coherent across different tools and conversations.

---

## Table of Contents

1. [Core Concepts](#1-core-concepts)
2. [Configuration](#2-configuration)
3. [Cross-Session Memory Recovery](#3-cross-session-memory-recovery)
4. [Multi-Tool Coexistence](#4-multi-tool-coexistence)
5. [Doctor Self-Diagnostics](#5-doctor-self-diagnostics)
6. [FAQ](#6-faq)

---

## 1. Core Concepts

### Memory is a Local Asset

Engram stores all data in the local `~/.engram/` directory. Any AI tool connected to the Engram MCP can read and write the same data. This means:

- Lessons written by Claude Code can be read immediately by Codex
- Decisions recorded by Cursor are visible to Claude Code in its next session
- It does not depend on any cloud sync — your memory belongs entirely to you

### Data Layers

| Layer | Description | Cross-Tool Visible | Cross-Session Persistent |
|----|------|-----------|-----------|
| **Identity** | Your role, preferences, tech stack | Yes | Yes |
| **Knowledge** | Lessons, decisions, playbooks | Yes | Yes |
| **Context** | Session context, recent operations | Yes | Yes |
| **Tool Registry** | Information about locally installed tools | Yes | Yes |

### source_tool Provenance

Every knowledge record has a `source_tool` field marking which tool wrote it. It is used for:
- Tracing knowledge provenance ("Was this lesson written by Codex or Claude Code?")
- Filtered viewing ("Show only Claude Code's lessons")
- Determining the authoritative source in case of conflict

---

## 2. Configuration

### 2.1 Basic Installation

Each AI tool needs to be configured with Engram as an MCP Server:

**Claude Code** - in `~/.claude/` or the project's `.mcp.json`:
```json
{
  "mcpServers": {
    "engram": {
      "command": "piia-engram-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

**Codex** - add an MCP server in the codex configuration pointing to the same command.

**Cursor** - add the same configuration in Cursor's MCP settings.

### 2.2 Shared Instructions File

Behavior rules shared by all tools go in:
```
~/.engram/shared_instructions.md
```

Each tool's private instructions go in its own configuration (e.g. `~/.claude/CLAUDE.md`). Shared instructions ensure all tools use Engram consistently.

### 2.3 Quick Context Snapshot

Engram automatically maintains a `~/.engram/quick_context.md` file — a snapshot of your identity card. AI tools can read this file directly on startup, without calling the MCP, achieving millisecond-level cold start.

---

## 3. Cross-Session Memory Recovery

### 3.1 Automatic Recovery Mechanism

When you start working in a new conversation, Engram provides three levels of context recovery:

| Level | Method | Content | Speed |
|------|------|------|------|
| **Quick** | Read `quick_context.md` | Identity + preferences + recent lessons | Milliseconds |
| **Resume** | `get_resume_brief()` | 30-second handoff + latest project/session context | <1s |
| **Standard** | `get_user_context(level="standard")` | Quick + decisions + project context | <1s |
| **Full** | `get_user_context(level="full")` | Standard + conflict detection + sync status | 1-2s |

**Recommended practice**:
- Most conversations: read `quick_context.md` directly (path 1)
- When switching tools or resuming yesterday's work: call `get_resume_brief()` first
- When deep context is needed: call `get_user_context(level="standard")` (path 2)
- Full review: use the `full` level only when explicitly needed

### 3.2 The 30-second handoff

`get_resume_brief()` now starts with a compact handoff section. It names the current project, the latest saved activity, the next action, and a trust note reminding the agent that stored memory is reference context rather than fresh user approval.

This is the recommended first call when moving between Claude Code, Codex, Cursor, Windsurf, or another MCP-compatible client:

1. The previous tool calls `wrap_up_session()` or `save_agent_context()`.
2. The next tool starts by calling `get_resume_brief()`.
3. The agent reads the handoff and suggested docs before asking the user to repeat context.

### 3.3 Metadata-only continuity proof

Use `engram continuity` when you want local proof that the handoff loop is ready without printing private memory bodies:

```bash
engram continuity --project /path/to/project
engram continuity --project /path/to/project --json
```

The report includes session counts, contributing tool names, whether at least two tools have saved context, whether `get_resume_brief()` can build, and aggregate recall-loop signals from local telemetry / beta event counters. It does not print session bodies, lesson text, decision reasoning, raw telemetry events, session IDs, or local project paths.

### 3.4 Session Saving

At the end of each important conversation, the AI tool should call:
```
save_agent_context(tool="claude_code", content="session summary...", project_folder="...")
```

This saves the conversation's key context as a persistent record, available for recovery next time.

### 3.5 Wrap-up Automatic Extraction

When `wrap_up_session` is called, Engram automatically:
1. Extracts lessons from the conversation content (marked as `tier: "staging"`)
2. Extracts key decisions
3. Saves the session context
4. Updates `quick_context.md`

Knowledge in the staging tier remains reviewable: you can promote, edit, archive, or reject it. In current releases, lessons and decisions can also be promoted to `verified` by the existing access-based promotion path after repeated use.

---

## 4. Multi-Tool Coexistence

The features in this section were introduced in v3.29.4 and remain supported in the current release.

### 4.1 Description Field Protection (v3.29.4+)

When multiple tools write to the profile's description field, Engram uses **append-merge** semantics:

- Tool A writes `"markA"` → description = `"markA"`
- Tool B writes `"markB"` → description = `"markA markB"` (appended, not overwritten)
- Tool A writes `"markA"` again → description unchanged (already exists, skipped)

This ensures that marks/information from multiple tools coexist without overwriting each other.

### 4.2 Field-Level Provenance (v3.29.4+)

The profile now records the last modification source of each field:

```json
{
  "role": "developer",
  "_provenance": {
    "role": {"by": "claude_code", "at": "2026-05-27T01:00:00"},
    "language": {"by": "codex", "at": "2026-05-27T02:00:00"}
  },
  "_last_updated_by": "codex"
}
```

Pass the `source_tool` parameter when calling `update_identity` to enable it:
```
update_identity(field="profile", updates_json='{"role":"developer"}', source_tool="claude_code")
```

### 4.3 Knowledge Deduplication (v3.29.4+)

When different tools write similar knowledge, Engram uses three-tier deduplication:

| Similarity | Handling | Description |
|--------|------|------|
| ≥ 85% | **Reject** | Exact duplicate, not added |
| 55%-84% | **Link** | Added but automatically linked via `related_ids` |
| < 55% | **Pass** | Added normally |

This means:
- Claude Code and Codex write the exact same lesson → only one is kept
- Writing similar but differing lessons → both are kept and automatically marked as related
- Writing unrelated lessons → each is stored independently

### 4.4 source_tool Filtering

View knowledge from a specific tool:
```
get_lessons(source_tool="claude_code")
get_decisions(source_tool="codex")
```

Searching also supports filtering by source:
```
search_knowledge(query="deployment", scope="lessons")
```

### 4.5 Conflicting Decision Management

When different tools or different points in time produce contradictory decisions (e.g. "deploy with Docker" vs "deploy on bare metal"), Engram detects the conflict and reports it in `get_user_context(level="full")`.

You can use `search_knowledge` to find the conflicting pair, then decide which to keep and which to archive.

---

## 5. Doctor Self-Diagnostics

The `doctor` MCP tool was introduced in v3.29.4 and remains supported in the current release. It lets you check the health of the memory system at any time.

### How to Invoke

In any AI tool connected to Engram, say:
> "Run Engram's doctor check for me"

The AI tool will call `doctor()` and return a report like the following:

| Check | Status | Details |
|--------|------|------|
| identity_completeness | PASS | profile complete |
| identity_provenance | PASS | field-level provenance enabled |
| knowledge_volume | PASS | lessons=42, decisions=15 |
| stale_knowledge | WARN | needs review: 12, archivable: 3 |
| near_duplicates | PASS | near-duplicate pairs: 2 |
| decision_conflicts | PASS | no conflicts |
| health_score | PASS | 87/100 |
| quick_context_freshness | PASS | last updated: 2.3 hours ago |
| encoding_health | PASS | no mojibake detected |

For terminal-side checks, `engram doctor` reports both the stored-data encoding health signal and the current terminal display encoding. If a Windows console or client previously wrote garbled Chinese into the store, run `engram repair-encoding` first to preview the affected fields, then `engram repair-encoding --apply` to repair reversible cases with a backup. If the store is clean but the terminal still displays mojibake, set `PYTHONIOENCODING=utf-8` for subprocess-heavy workflows.

Terminal `engram doctor` also includes a config integrity section. It reports metadata-only counts and short hashes for known MCP configs, AI instruction files, shared instruction files, and Claude Code hook settings, plus the number of project rule files found. It does not print config bodies, hook commands, instruction bodies, or project rule lines, so the output is suitable for local diagnostics and easier to sanitize before sharing.

### JSON Output Supported

```
doctor(output_format="json")
```

Returns structured JSON for easy automated processing.

---

## 6. FAQ

### Q: Will two tools writing at the same time conflict?

Engram uses file-level locking (portalocker) to prevent concurrent writes from corrupting data. Only one process can write to the same file at any given moment. The writes from two tools are serialized, so no data is lost.

### Q: I switched AI tools — is my previous memory still there?

Yes. All data is stored under `~/.engram/`, and any tool connected to the Engram MCP can access it. Even if you switch from Claude Code to Codex and then to Cursor, the memory is exactly the same.

### Q: How do I know which tool wrote a particular piece of knowledge?

Every knowledge record has a `source_tool` field. You can filter the view with `get_lessons(source_tool="claude_code")`. The Identity Card (`get_identity_card()`) also annotates the source.

### Q: Does Quick Context update automatically?

Yes. `quick_context.md` is automatically refreshed every time `wrap_up_session` or `get_user_context` is called.

### Q: How do I clean up stale knowledge?

1. Call `doctor()` to see which knowledge is stale
2. Call `knowledge_overview()` to get a detailed lifecycle report
3. Call `archive_lesson(id)` to archive stale entries
4. Or use the Review UI: `request_outline_review()` generates a visual review interface

### Q: Will cross-session context grow without limit?

No. Engram has the following mechanisms to control growth:
- A maximum of 200 entries per knowledge type (`MAX_KNOWLEDGE_ENTRIES`)
- When the limit is exceeded, the staging tier is evicted first → then the oldest verified tier
- Stale knowledge decays differentially by type (user preferences 90 days, debug tips 15 days)
- Knowledge extracted by `wrap_up_session` defaults to staging, and is only promoted after being accessed multiple times

### Q: If multiple tools modify identity at the same time, will it overwrite?

Since v3.29.4, the description field uses append semantics and will not be overwritten. Other fields (role, language, etc.) are still last-write-wins, but with `_provenance` tracking so you can trace who changed them.

---

## Appendix: Type-Aware Expiration Policy

Different types of knowledge have different expiration cycles (introduced in v3.29.4 and still supported):

| Knowledge Domain | Review Cycle | Archive Cycle | Description |
|----------|----------|----------|------|
| user_preference | 90 days | 180 days | User preferences change slowly |
| architecture | 60 days | 120 days | Architecture decisions are relatively stable |
| strategy | 60 days | 120 days | Strategic direction |
| product | 45 days | 90 days | Product decisions |
| workflow | 30 days | 60 days | Workflow (default) |
| debug | 15 days | 30 days | Debug tips decay quickly |
| config | 15 days | 30 days | Configuration issues decay quickly |

Domains are matched via the `domain` field. Unmatched entries default to the 30/60 day cycle.
