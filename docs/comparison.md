# Engram vs. other memory / identity tools

This page is a **factual** comparison of where Engram sits in the AI-memory space. It is **not** a marketing pitch — we link to each project's own docs and call out where they're stronger than us.

> Last reviewed: 2026-06-08. We re-check this each minor release. If you spot an inaccuracy, please open an issue.

---

## The space, in one sentence

A common approach to "AI memory" is to store **what the agent did**. Engram takes the other angle: it stores **who the user is** — identity, preferences, quality standards, and lessons that survive across every tool the user ever uses. AI proposes knowledge; high-risk items are staged for your review, and everything stays visible, editable, and reversible.

If you only need a single agent to remember its own conversations, you don't need Engram. If you want your identity to follow you from Claude Code to Cursor to Codex without re-training each one, Engram is built for that.

---

## Three categories of AI memory

The AI memory space has three distinct categories. Most confusion comes from treating them as one.

### 1. Agent memory — what the agent did

Tools that store task context, conversation history, and session state **for the agent**. The agent writes and reads its own memory automatically.

| Project | Stars | Storage | Auto-capture | Governance |
|---|---|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) | 56k | Vector DB / cloud | Strong | None |
| [MemPalace](https://github.com/MemPalace/mempalace) | 52k | ChromaDB / pluggable | Strong (hooks) | None |
| [Graphiti](https://github.com/getzep/graphiti) | 26k | Temporal knowledge graph | Strong | None |
| [Letta](https://github.com/letta-ai/letta) | 22k | Postgres / cloud | Agent self-edit | Agent-owned |
| [agentmemory](https://github.com/rohitg00/agentmemory) | 16k | SQLite / Postgres / KG | Strong (hooks) | Weak |
| [memU](https://github.com/NevaMind-AI/memU) | 13k | Vector / KG | Strong | None |
| [MemOS](https://github.com/MemTensor/MemOS) | 9k | Memory OS abstraction | Strong | None |

**When to use these:** You need an agent to remember its own work across sessions, build knowledge graphs from conversations, or do semantic retrieval over large document sets.

**Why not Engram:** Engram doesn't do agent self-editing memory. These tools are better at that.

### 2. Project / repo memory — what happened in this codebase

Tools that store project-specific context: codebase structure, repo conventions, coding decisions within a project.

| Project | Stars | Storage | Focus |
|---|---|---|---|
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | 3k | Markdown + KG | Zettelkasten-style knowledge |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | 2.5k | Code intelligence graph | 155-language code indexing |
| [MemSearch](https://github.com/zilliztech/memsearch) | 1.8k | Markdown + Milvus | Unified memory via vector DB |
| [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | 1.8k | SQLite / vector / KG | 14+ AI client support |
| [Nocturne Memory](https://github.com/Dataojitori/nocturne_memory) | 1.1k | Graph-like structured | Rollbackable / visual |
| [Context Portal](https://github.com/GreatScottyMac/context-portal) | 764 | SQLite project KG | Project-specific RAG |
| Config files (AGENTS.md, CLAUDE.md, .cursorrules) | n/a | Plain text | Static per-repo rules |

**When to use these:** You need project-specific conventions, code indexing, or repo-scoped knowledge graphs.

**Why not Engram:** Engram stores *you*, not your repo. Use AGENTS.md for repo rules, Engram for personal knowledge. They work together — `engram setup` auto-injects instruction snippets into your existing CLAUDE.md, .cursorrules, and AGENTS.md files.

### 3. Cross-tool personal identity — who you are

Tools that store the **user's** identity, preferences, and accumulated knowledge across multiple AI tools.

| Project | Stars | Storage | Governance | Unique angle |
|---|---|---|---|---|
| **piia-engram** | (this project) | Local JSON | **risk-gated staging → verified** (high-risk needs approval; all reversible) | Identity layer: lessons, decisions, playbooks |
| [OpenMemory](https://mem0.ai/openmemory) | Mem0 ecosystem | Local-first MCP memory | Memory controls in app | Cross-client memory layer for coding agents |
| [Gentleman Engram](https://github.com/Gentleman-Programming/engram) | 3.7k | SQLite + FTS5 | None | Go single binary, 8+ tools |
| [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | 1.8k | SQLite / vector / KG | Weak | 14+ client support |
| [ByteRover](https://github.com/campfirein/byterover-cli) | 4.7k | Portable memory layer | Weak | "Portable memory layer" narrative |
| [Remnic](https://github.com/joshuaswarren/remnic) | 74 | SQLite + vector | Provenance / correction | User-aware agents |
| [@modelcontextprotocol/server-memory](https://github.com/modelcontextprotocol/servers) | 86k* | Local knowledge graph | None | Official reference implementation |

*\* monorepo star count; individual memory server is one of many packages*

**This is where piia-engram lives.** Among the projects we've surveyed, piia-engram makes a **risk-gated staging → verified** model the central data model across tools: low/medium-risk knowledge is auto-verified with an audit entry, while high-risk items (and unsupervised background or LLM-extracted writes) land in a review tier for your approval. Everything stays visible, editable, and reversible, and a future opt-in strict mode can route *all* writes to review. OpenMemory is the closest direct product comparison for a local-first MCP memory layer; piia-engram's narrower angle is the user-owned identity layer: preferences, standards, lessons, decisions, and playbooks that remain portable across tools.

**Naming note:** [Gentleman-Programming/engram](https://github.com/Gentleman-Programming/engram) is an unrelated project. It is a single Go binary with SQLite + FTS5, MCP/HTTP/CLI/TUI surfaces, and a different product shape. piia-engram is the Python package on PyPI and the user-owned identity layer described here; the two projects are not affiliated.

---

## vs. native coding-tool memory

Claude Code, OpenAI Codex, Cursor, and Windsurf are all improving their own memory, rules, and context systems. We see that as validation of the problem, not as something Engram should replace.

Native memory is best when you live inside one tool. Engram is for the moment you switch tools and want the same approved identity, preferences, lessons, and decisions to follow you.

| Tool | Native memory / rules | Scope | Where it is stronger | Where Engram complements it |
|---|---|---|---|---|
| [Claude Code memory](https://code.claude.com/docs/en/memory) | `CLAUDE.md`, rules, auto memory | Claude Code projects and user config | Deep native integration with Claude Code | Shares stable user identity with Codex, Cursor, Windsurf, and MCP tools |
| [OpenAI Codex memories](https://developers.openai.com/codex/memories) | Codex-local memories | Codex sessions | Native Codex recall and workflow fit | Keeps user identity portable outside Codex |
| [AGENTS.md](https://developers.openai.com/codex/guides/agents-md) | Repo instructions | Repository scope | Version-controlled project rules | Engram stores personal cross-repo preferences and lessons |
| [Cursor memories](https://docs.cursor.com/en/context/memories) | Memories and rules | Cursor projects | Low-friction IDE-native continuity | Gives Cursor the same approved user identity other tools see |
| [Windsurf memories](https://docs.windsurf.com/windsurf/cascade/memories) | Cascade memories and rules | Windsurf workspace | Local IDE memory for Cascade workflows | Keeps durable personal context independent of one workspace |

**How to think about it:** use native memory for tool-specific workflow state. Use AGENTS.md / CLAUDE.md / rules for repo instructions. Use piia-engram for the person behind those repos and tools.

---

## vs. config files (AGENTS.md / CLAUDE.md / .cursorrules)

The most common question: **"Why not just use AGENTS.md?"**

Fair question. Here's the honest answer.

| | AGENTS.md | CLAUDE.md | .cursorrules | piia-engram |
|---|---|---|---|---|
| **Scope** | Per-repo | Global or per-project | Per-project | **Per-user, all projects** |
| **Works in** | Codex | Claude Code | Cursor | Claude Code, Codex, Cursor, Windsurf, and any MCP tool |
| **Content** | Free-text instructions | Free-text instructions | Free-text rules | Structured: profile, lessons, decisions, playbooks |
| **Searchable** | ❌ AI reads the whole file | ❌ AI reads the whole file | ❌ AI reads the whole file | ✅ Weighted search, project-aware filtering |
| **Learns over time** | ❌ You edit manually | ❌ You edit manually | ❌ You edit manually | ✅ AI proposes; you can review, edit & roll back |
| **Cross-tool** | ❌ | ❌ | ❌ | ✅ |
| **Survives tool switch** | ❌ Stays in the repo | ❌ Stays in Claude Code | ❌ Stays in Cursor | ✅ Follows you |
| **Project knowledge** | ✅ (repo-specific) | ⚠ (project dir) | ✅ (repo-specific) | ✅ (via project snapshots) |

### When config files are enough

- You use **one AI tool** and don't plan to switch.
- Your instructions are **project-specific** (build steps, repo conventions).
- You don't need to accumulate knowledge over time — you just need a static prompt.

**In these cases, use the config file.** It's simpler. No MCP needed.

### When you need piia-engram

- You use **2+ AI tools** and want them to share the same context about you.
- You want your **personal** preferences and lessons to follow you across repos — not be copy-pasted into every project.
- You want to **accumulate** knowledge (lessons, decisions, playbooks) over months, not rewrite instructions from scratch.
- You want the AI to **search** relevant knowledge instead of reading a giant file.

### They work together

piia-engram doesn't replace AGENTS.md — it complements it. Use AGENTS.md for **repo-specific** rules ("this project uses tabs, runs on Python 3.11"). Use piia-engram for **you** ("I prefer concise responses, I've learned X, I decided Y").

In fact, `engram setup` **auto-injects a small instruction snippet** into your existing CLAUDE.md, .cursorrules, and AGENTS.md files. This snippet tells the AI to call Engram at conversation start — so the two systems work together automatically. The snippet is clearly marked and can be removed with one function call.

---

## Where competitors are stronger (honest assessment)

We believe in honest positioning. Here's where other tools beat us today:

| Area | Who does it better | Why |
|---|---|---|
| **Installation simplicity** | Single-binary projects (Go / Rust) | Single binary / `brew install` is one command. piia-engram requires `pip install` + MCP config (also one-time, but two steps). |
| **Auto-capture via hooks** | Projects with native shell-hook integrations | Hooks bypass the "AI forgets to call the tool" problem. piia-engram now uses Claude Code Stop / PreCompact / SessionStart / PostCompact hooks for the same goal, plus instruction injection into config files for tools without hook APIs. |
| **Semantic retrieval** | Vector-DB-based memory tools | Vector DB + embeddings tend to score higher on benchmark recall. piia-engram uses character n-gram + alias tokenization — deterministic, offline, CJK-friendly, tuned for the personal-identity store size (see [Scale & retention](#scale--retention) below). |
| **Benchmark narrative** | Projects publishing LongMemEval scores | piia-engram focuses on **governance metrics** (precision of user-approved knowledge, conflict rate, stale-decay accuracy) rather than recall benchmarks, because the use case is "right thing surfaced" not "everything indexed". |
| **Visual experience** | Projects with dedicated dashboards | piia-engram has a CLI + a generated HTML review page. A dedicated GUI is not currently on the roadmap. |
| **Ecosystem scale** | Mainstream memory frameworks | piia-engram is a smaller, focused project. Larger ecosystems have more integrations, plugins, and community tooling. |

---

## What Engram explicitly does *not* do

- **No vector embeddings.** We use character n-gram + alias tokenization for similarity. This is fast, deterministic, works offline, and handles CJK well. It's tuned for a personal-identity store, not a large document corpus — see [Scale & retention](#scale--retention) for the sizing detail.
- **No cloud storage in core.** There is no Engram Cloud and no managed identity or knowledge store. **Usage statistics are off by default** — users must explicitly opt in during `engram setup` or with `engram telemetry on`. Local telemetry writes anonymous aggregated counts (tool names + call counts, knowledge totals, Engram version) to `~/.engram/telemetry.log` and makes no network requests by itself. Remote telemetry (`engram telemetry remote on`) and weekly feedback (`engram telemetry feedback on`) are separate explicit opt-ins and send only count-only / metadata-only payloads. Identity content, prompts, project paths, file paths, credentials, free-text content, error text, stack traces, and stable cross-day user IDs are not collected. The optional `read_web_content` tool makes outbound HTTP only when explicitly invoked for a URL. MCP transport itself is stdio or self-hosted HTTP.
- **No silent, unauditable memory writes.** The agent can call `add_lesson` / `add_decision` / `extract_session_insights`, and every write passes through a risk gate: low/medium-risk items are auto-verified with an audit entry, while high-risk items are held in `staging` for your approval. Unsupervised background writeback and LLM-extracted suggestions are forced to `staging` regardless of risk and cannot self-label as `verified`. Everything is visible, editable, and reversible from the review page, and an opt-in strict mode can route *all* writes to review. This guards against the failure mode where an agent hallucinates a "remembered fact."
- **No team / multi-user model.** Engram is one person × many tools. If you need many people × many tools, you want something else.

---

## Why "identity layer" not "memory layer"

This is the architectural call that drives every other choice.

| **Memory layer** thinking | **Identity layer** thinking |
|---|---|
| Store the agent's working state | Store the user's stable preferences |
| Optimize for recall accuracy | Optimize for cold-start onboarding |
| Per-agent / per-conversation scope | Per-user, cross-tool scope |
| Grows linearly with usage | Bounded by user's actual identity |
| Vector store is the natural shape | Curated structured store is the natural shape |
| Agent owns it | User owns it; agents contribute proposals |

Letta and Mem0 are the canonical examples of the memory-layer approach. They're excellent at it. Engram is the canonical example of the identity-layer approach — they're complementary, not competitive.

You could absolutely run **Engram + Letta + Mem0** together: Engram for who you are, Letta for what the agent is doing right now, Mem0 for the team's shared document corpus.

---

## Scale & retention

A personal-identity store stays small by design. Engram's default sizing target is a few hundred items per knowledge kind (lessons, decisions, playbooks) — the natural shape of *who you are and what you've learned*, not a record of every interaction. The character-n-gram retrieval, JSON storage, and review queue are all tuned for that shape.

The default threshold is configurable, and stale-decay + archive-knowledge keep older entries from accumulating. If you find yourself wanting a five-figure document corpus, Engram is the wrong layer for that — pair it with a vector-store memory tool (Mem0, Letta, etc.) for the bulk side.

---

## See also

- [README](../README.md) — what Engram is and how to install
- [Trust model](trust.md) — local-first boundaries, approval workflow, and report-sharing guidance
- [Cross-tool continuity demo](cross-tool-continuity-demo.md) — safe synthetic proof of the handoff workflow
- [architecture.md](architecture.md) — internal structure of Engram itself
