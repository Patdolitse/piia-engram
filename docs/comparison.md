# Engram vs. other memory / identity tools

This page is a **factual** comparison of where Engram sits in the AI-memory space. It is **not** a marketing pitch — we link to each project's own docs and call out where they're stronger than us.

> Last reviewed: 2026-05-24. We re-check this each minor release. If you spot an inaccuracy, please open an issue.

---

## The space, in one sentence

Most "AI memory" tools store **what the agent did**. Engram stores **who the user is** — identity, preferences, quality standards, and lessons that survive across every tool the user ever uses.

If you only need a single agent to remember its own conversations, you don't need Engram. If you want your identity to follow you from Claude Code to Cursor to Codex without re-training each one, Engram is built for that.

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

## vs. AI memory tools

## At a glance

| Capability | Engram | Letta (MemGPT) | Mem0 | Cline `memories/` | Claude Code `~/.claude/projects/*/memory/` |
|---|---|---|---|---|---|
| **Primary model** | User identity + lessons across tools | Agent self-edit + recall | Agent vector + KV store | Per-conversation notes (Cline only) | Per-project notes (Claude Code only) |
| **Cross-tool by design** | ✅ MCP-native, 43 tools | ⚠ via API, requires custom wiring per tool | ⚠ via SDK, requires custom wiring per tool | ❌ Cline-specific | ❌ Claude Code-specific |
| **Storage location** | Local JSON in `~/.engram/` | Postgres (self-host) or Letta Cloud | Vector DB (Qdrant, etc.) + Mem0 Cloud | Local files in the project | Local files under home dir |
| **Local-first** | ✅ default; cloud is opt-in (none today) | ⚠ self-host possible; Cloud is the default narrative | ⚠ self-host possible; Cloud is the default narrative | ✅ | ✅ |
| **Network dependency** | Only `read_web_content` (optional) | API call per memory op | API call per memory op | None | None |
| **Embeddings / vector DB** | ❌ deliberately not — n-gram + alias tokenization | ✅ pluggable | ✅ pluggable | ❌ | ❌ |
| **Encryption at rest** | ✅ AES-256-GCM, PBKDF2 600k (opt-in via `ENGRAM_SECRET`) | depends on Postgres/Cloud config | depends on store + Cloud config | ❌ plain Markdown | ❌ plain Markdown |
| **MCP-native** | ✅ built around MCP | ⚠ third-party MCP wrappers exist | ⚠ third-party MCP wrappers exist | n/a | n/a |
| **Cold-start context budget** | ✅ token-budgeted, priority + display ordering | ✅ but consumes its own LLM cycles for self-editing | ⚠ retrieval, not budget shaping | ❌ raw file load | ❌ raw file load |
| **Conflict detection** | ✅ surfaces contradictory decisions/lessons | ❌ | ❌ | ❌ | ❌ |
| **User-facing audit** | ✅ HTML review page with rarity tiers + opt-in audit log | ⚠ via UI in Letta Cloud | ⚠ via Mem0 dashboard | ✅ files are plain Markdown | ✅ files are plain Markdown |
| **Knowledge tiers** | ✅ staging → verified, manual promotion gate | ❌ | ❌ | ❌ | ❌ |
| **OSS license** | Apache-2.0 | Apache-2.0 | Apache-2.0 | Apache-2.0 | Anthropic ToS |
| **Total tests** | 791, 96% coverage (v3.29.0) | (varies; not our number to publish) | (varies; not our number to publish) | n/a | n/a |

External docs we drew from:
- Letta: <https://github.com/letta-ai/letta>, <https://docs.letta.com>
- Mem0: <https://github.com/mem0ai/mem0>, <https://docs.mem0.ai>
- Cline `memories/`: <https://docs.cline.bot/features/memory>
- Claude Code memory: <https://docs.anthropic.com/en/docs/claude-code/memory>

---

## When to choose Engram

✅ **Choose Engram when:**
- You use **more than one** AI coding tool (Claude Code + Cursor + Codex + …) and want them to share your identity, preferences, and lessons.
- You want **local-first by default** — no API calls per memory operation, no cloud lock-in.
- You want a **user-curated knowledge base**, not a vector dump. The staging/verified tier + review page are designed around the user being the editor.
- You're building **agent-to-tool workflows over MCP** and want a turnkey identity surface.

⚠ **Choose something else when:**
- **You want a single agent to remember its own conversation context across sessions.** That's what Letta/MemGPT does well — its "self-editing memory" model is purpose-built for agent autonomy. Engram doesn't do that.
- **You need semantic search over a million-document corpus.** Mem0 (with a vector DB backend) is built for that. Engram intentionally uses n-gram + alias tokenization because the typical Engram store has 200–500 items, where exact-ish recall beats embeddings.
- **You're inside a single tool (Cline-only, Claude-Code-only) and don't plan to switch.** The native per-tool memory will be lighter-weight and tighter-integrated than wiring up Engram.
- **You need a managed multi-user SaaS with team sharing.** Engram is single-user, local. No team features.

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
- [milestone_review_v3.13.2.md](milestone_review_v3.13.2.md) — external AI audit that drove the v3.14.x positioning work
