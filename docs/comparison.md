# Engram vs. other memory / identity tools

This page is a **factual** comparison of where Engram sits in the AI-memory space. It is **not** a marketing pitch — we link to each project's own docs and call out where they're stronger than us.

> Last reviewed: 2026-05-24, based on a [37-project competitive scan](../experiments/competitive_landscape_2026Q2.md). We re-check this each minor release. If you spot an inaccuracy, please open an issue.

---

## The space, in one sentence

Most "AI memory" tools store **what the agent did**. Engram stores **who the user is** — identity, preferences, quality standards, and lessons that survive across every tool the user ever uses. AI proposes knowledge; you approve what becomes permanent.

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
| **piia-engram** | 80 | Local JSON | **staging → verified** (user approves) | Identity layer: lessons, decisions, playbooks |
| [Gentleman Engram](https://github.com/Gentleman-Programming/engram) | 3.7k | SQLite + FTS5 | None | Go single binary, 8+ tools |
| [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | 1.8k | SQLite / vector / KG | Weak | 14+ client support |
| [ByteRover](https://github.com/campfirein/byterover-cli) | 4.7k | Portable memory layer | Weak | "Portable memory layer" narrative |
| [Remnic](https://github.com/joshuaswarren/remnic) | 74 | SQLite + vector | Provenance / correction | User-aware agents |
| [@modelcontextprotocol/server-memory](https://github.com/modelcontextprotocol/servers) | 86k* | Local knowledge graph | None | Official reference implementation |

*\* monorepo star count; individual memory server is one of many packages*

**This is where piia-engram lives.** No other project in this category uses staging → verified as a default governance model. The closest are Remnic (provenance/correction) and Vestige (contradiction/audit).

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
| **Learns over time** | ❌ You edit manually | ❌ You edit manually | ❌ You edit manually | ✅ AI proposes, you review |
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
| **Installation simplicity** | Gentleman Engram, Vestige, remindb | Single binary / `brew install`. piia-engram requires `pip install` + MCP config |
| **Auto-capture via hooks** | MemPalace, claude-memory-compiler, ClawMem | Hooks bypass "AI forgets to call the tool" problem. piia-engram uses instruction injection (v3.29.0+) |
| **Semantic retrieval** | Mem0, Graphiti, agentmemory | Vector DB + embeddings. piia-engram uses n-gram search (fast, offline, but less "smart") |
| **Benchmark narrative** | MemPalace (96.6%), Mem0 (94.8%) | LongMemEval scores. piia-engram focuses on governance metrics, not recall benchmarks |
| **Visual experience** | Vestige (3D dashboard), Nocturne (rollback UI) | piia-engram has CLI + HTML review page |
| **Ecosystem scale** | Mem0 (742k weekly downloads), Graphiti (146k) | piia-engram has ~4k weekly downloads |

---

## What Engram explicitly does *not* do

- **No vector embeddings.** We use character n-gram + alias tokenization for similarity. This is fast, deterministic, works offline, handles CJK well, and is appropriate for the small-store regime (200–500 items). It would be the wrong choice at 100,000 items — don't use Engram for that.
- **No cloud storage in core.** There is no Engram Cloud, no managed instance. **Usage statistics are off by default** — users must explicitly opt in during `engram setup`. When enabled, only anonymous aggregated counts are sent (tool call counts, knowledge totals, engram version); no identity content, prompts, file paths, or IP addresses are ever transmitted. The only other network call from the core library is `read_web_content` (optional, requires the local Engram Reader sidecar). MCP transport itself is stdio or self-hosted HTTP.
- **No automatic "agent self-edits the memory."** The agent can call `add_lesson` / `add_decision` / `extract_session_insights`, but new items land in the `staging` tier. They only become `verified` when the user explicitly promotes them via the review page. This is a deliberate choice against the failure mode where an agent hallucinates a "remembered fact."
- **No team / multi-user model.** Engram is one person × many tools. If you need many people × many tools, you want something else.

---

## Why "identity layer" not "memory layer"

This is the architectural call that drives every other choice.

| **Memory layer** thinking | **Identity layer** thinking |
|---|---|
| Store the agent's working state | Store the user's stable preferences |
| Optimize for recall accuracy | Optimize for cold-start onboarding |
| Per-agent / per-conversation scope | Per-user, cross-tool scope |
| Grows linearly with usage | Bounded by user's actual identity (~hundreds of items) |
| Vector store is the natural shape | Curated structured store is the natural shape |
| Agent owns it | User owns it; agents contribute proposals |

Letta and Mem0 are the canonical examples of the memory-layer approach. They're excellent at it. Engram is the canonical example of the identity-layer approach — they're complementary, not competitive.

You could absolutely run **Engram + Letta + Mem0** together: Engram for who you are, Letta for what the agent is doing right now, Mem0 for the team's shared document corpus.

---

## See also

- [README](../README.md) — what Engram is and how to install
- [architecture.md](architecture.md) — internal structure of Engram itself
- [competitive_landscape_2026Q2.md](../experiments/competitive_landscape_2026Q2.md) — full 37-project competitive scan with data cards
- [milestone_review_v3.13.2.md](milestone_review_v3.13.2.md) — external AI audit that drove the v3.14.x positioning work
