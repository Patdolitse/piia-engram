# Listing copy

This page keeps short, reusable public descriptions for package indexes, MCP directories, release notes, and listing pages. Keep the wording factual, current, and aligned with the README.

## Positioning baseline

One line:

> Local-first personal AI identity and memory for MCP-compatible coding tools.

Expanded:

> piia-engram keeps approved preferences, lessons, decisions, playbooks, and project context portable across MCP-compatible coding tools. It stores data locally, requires no cloud account, and keeps AI-suggested memories reviewable before they become durable.

Do not position piia-engram as:

- a cloud memory API;
- a replacement for AGENTS.md, CLAUDE.md, or editor rules;
- a general vector database;
- a team knowledge base;
- a promise that unsupported tools will remember everything automatically.

## English copy

### One-line description

Local-first personal AI identity and memory for MCP-compatible coding tools, with user-approved lessons, decisions, and project context.

### Short listing

piia-engram is a local-first personal AI identity layer for MCP-compatible coding tools. It stores your preferences, quality standards, lessons, decisions, playbooks, and project snapshots as user-owned local files. AI tools can propose knowledge, but durable memory stays reviewable and user-approved.

### Medium listing

piia-engram gives AI coding tools a shared, user-owned identity layer. Instead of re-explaining your preferences, quality bar, past lessons, and project decisions in every new chat, store them once as local JSON/Markdown under `~/.engram/`. Claude Code, Codex, Cursor, Windsurf, Claude Desktop, and other MCP-compatible tools can start from the same approved context. New AI suggestions go to review before becoming verified memory. No cloud account is required.

### Long listing

piia-engram is a local-first personal AI identity and memory layer for developers who use more than one AI coding tool. Native memories in Claude Code, Codex, Cursor, and Windsurf are useful, but they are usually scoped to one product or workspace. piia-engram keeps the stable parts of you above those tools: your communication preferences, coding standards, lessons learned, key decisions, playbooks, and project snapshots.

All core identity and knowledge data lives on your machine under `~/.engram/` as JSON/Markdown files. AI tools can propose new lessons or decisions, but durable memory follows a staging-to-verified workflow so the user remains in control. The project is MCP-native, Apache 2.0, and designed to complement AGENTS.md, CLAUDE.md, editor rules, and agent memory systems such as Mem0, Zep, and Letta.

Use piia-engram when you want Claude Code, Codex, Cursor, Windsurf, Claude Desktop, or another MCP client to begin from the same approved understanding of who you are and how you work.

## Chinese copy

### 短描述

piia-engram 是本地优先的个人 AI 身份层，让你的偏好、经验、决策和项目上下文在 Claude Code、Codex、Cursor、Windsurf 以及 MCP 工具之间延续。AI 可以建议记忆，只有你确认后才成为长期事实。

### 中描述

piia-engram 面向同时使用多个 AI 编程工具的开发者。它把你的身份、质量标准、经验教训、关键决策和项目上下文保存为本机 `~/.engram/` 下的 JSON/Markdown 文件，并通过 MCP 让 Claude Code、Codex、Cursor、Windsurf 等工具读取同一份已确认上下文。它不是 agent memory 数据库，也不是 AGENTS.md / CLAUDE.md 的替代品，而是位于这些工具之上的、用户拥有的个人身份资产。

## Channel notes

### PyPI

Use the project description from `pyproject.toml` for the metadata line. The long description comes from `README.md`, so keep the README first screen aligned with this file.

Recommended metadata description:

> Local-first personal AI identity and memory for MCP-compatible coding tools, with user-approved lessons, decisions, playbooks, and project context.

### MCP Registry

The registry description should be short and literal. Avoid slogans that imply unlimited tool coverage.

Recommended `.mcp/server.json` description (the MCP Registry currently requires this field to be 100 characters or fewer):

> Local-first AI identity for MCP coding tools. User-approved lessons, decisions, and context.

### mcpservers.org

Use the short listing plus three trust bullets:

- Local files under `~/.engram/`.
- No cloud account required.
- AI suggestions stay reviewable before becoming verified memory.

### GitHub release notes

GitHub release notes should be bilingual when the release body is written by
project maintainers: English first, then Chinese, separated into two clear
sections. Keep the tag and release title ASCII/English for ecosystem indexing.

Recommended structure:

```text
## English

[English release summary, highlights, upgrade notes, and verification evidence.]

## Chinese

[Chinese release summary, highlights, upgrade notes, and verification evidence.]
```

When cutting the next positioning release, lead with positioning rather than features in both sections:

> This release sharpens piia-engram's public positioning as a local-first personal AI identity layer for MCP-compatible coding tools. It updates README messaging, adds a trust model, expands comparison coverage for OpenMemory and native coding-tool memories, and documents a cross-tool continuity demo.

## Keywords

Suggested keywords:

- ai identity
- personal AI memory
- local-first
- MCP server
- cross-tool memory
- coding agents
- Claude Code
- Codex
- Cursor
- Windsurf
- user-approved memory
- developer preferences
- lessons learned
- decisions
- project context

Keep `memory` in the keyword set for discovery, but avoid making "memory database" the primary category.
