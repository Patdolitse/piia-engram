# Engram User Guide

> 中文版：[Engram 用户指南](user-guide.zh-CN.md)
>
> This guide applies to current releases. It is a behavior-first overview: what
> Engram does, what you actually do, and what never happens without your say-so.
> For the 5-minute path, start with the
> [Quickstart](quickstart-first-value.md). For data boundaries, see the
> [Trust model](trust.md).

Engram is a **local-first personal memory and identity layer for AI tools**. It
lets Claude Code, Codex, Cursor, Windsurf, Claude Desktop, and other
MCP-compatible tools share the same approved context about you — preferences,
standards, lessons, decisions, playbooks, and project snapshots — so you stop
re-explaining yourself every session and every time you switch tools.

---

## 0. The mental model: what Engram is, and what it is not

Read this first. It removes most of the confusion about "what is running."

**Engram is not a background daemon.** Nothing runs 24/7. There is no agent
quietly watching your machine. Engram is three things working together:

1. **A local file store** at `~/.engram/` (plain JSON and Markdown). This is the
   single source of truth, and it belongs to you.
2. **A set of MCP tools** your AI clients can call to read and write that store.
3. **Instruction rules** in your tools' global config (e.g. `~/.claude/CLAUDE.md`,
   `AGENTS.md`) that tell the AI *when* to call those tools.

So when something looks "automatic," what actually happened is: your AI tool —
following its instruction rules — chose to call an Engram MCP tool, which read
or wrote a local file. **If no AI tool is open, nothing happens.** This is by
design: it keeps the system transparent, inspectable, and fully under your
control.

| Common assumption | Reality |
|---|---|
| "Engram syncs to a cloud account." | No cloud account, no required login, no default cloud sync. Data is local. |
| "It records everything I do automatically." | It only records when an AI tool calls a write tool, usually because you asked or a rule fired. |
| "A service indexes my files in the background." | Indexing and dedup run *inside* a tool call, on demand — not in a background process. |
| "AI can silently promote anything to trusted memory." | High-risk writes are gated; unsupervised writeback is forced to staging (see §4). |

---

## 1. Install and connect

```bash
pip install piia-engram
engram setup
```

`engram setup` detects your AI clients, **shows you the exact config files it
will touch, and asks for one-keystroke confirmation before writing** the MCP
connection. Every external write is backed up first, and declining leaves all
configs untouched. For non-interactive/CI runs, `engram setup
--apply-external-config` skips the prompt.

By default you get **17 core MCP tools** (`ENGRAM_TOOLS=core`) — enough for
install, first value, daily recall, and session wrap-up. The advanced set
(review queues, import/export, governance, migration, Playbook management) stays
off until you opt in with `ENGRAM_TOOLS=all`.

After connecting once, **auto-bootstrap** does the rest: the first time your AI
tool calls Engram (`get_user_context` or `get_resume_brief`), it scans your
existing rule files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, …) **read-only**
and imports your preferences and project rules automatically — no separate
import step.

- Host-specific setup: [Claude Code](integrations/claude-code.md) ·
  [Codex](integrations/codex.md) · [Cursor](integrations/cursor.md) ·
  [Hermes](integrations/hermes.md)
- Verify health anytime with `engram doctor`.

---

## 2. Your first value, in one session

The point of Engram shows up the *second* time you talk to an AI — when it
already knows something you told it before. To feel it once:

1. In a connected tool, give it one stable preference, e.g.
   *"Remember that I prefer concise answers with explicit verification commands."*
   The AI calls a write tool (`memory_store`, `add_lesson`, `add_decision`,
   `add_playbook`, or `update_identity`).
2. Start a **fresh** chat — in the same tool, or a different connected tool on
   the same machine.
3. Ask something where that preference applies. The new session starts from what
   you already said, instead of asking you to re-explain.

If recall does not fire, make it explicit once
(*"Use Engram to search my saved preference about concise answers"*) and see the
[Quickstart troubleshooting section](quickstart-first-value.md#if-recall-did-not-fire).

---

## 3. Cross-tool and cross-session continuity

Because every tool reads and writes the same `~/.engram/` store, a lesson
written by Claude Code is immediately visible to Codex, and a decision recorded
in Cursor shows up in Claude Code's next session. No cloud sync involved.

The recommended handoff loop when moving between tools or resuming yesterday's
work:

1. The previous tool calls `wrap_up_session()` (or `save_agent_context()`) to
   save the session.
2. The next tool starts by calling `get_resume_brief()` — a 30-second handoff
   naming the current project, last activity, next action, and a trust note.
3. The agent reads the handoff before asking you to repeat context.

`wrap_up_session` is a lightweight session-end save. It does not run full
reconciliation by default. Use `run_reconcile=True` only for owner-approved
maintenance reconciliation.

Three levels of recovery, fastest first:

| Level | How | Speed |
|---|---|---|
| Quick | Read `~/.engram/quick_context.md` directly | milliseconds |
| Resume | `get_resume_brief()` | <1s |
| Standard | `get_user_context(level="standard")` | <1s |
| Full | `get_user_context(level="full")` (adds conflicts + sync) | 1–2s |

Every record carries a `source_tool` field so you can always trace which tool
wrote it. For the full treatment — multi-tool coexistence, identity-field
provenance, conflict handling, and a metadata-only continuity proof — see the
[Cross-tool guide](cross-tool-guide.md).

---

## 4. Governance and approval: AI suggests, you review what matters

Engram treats durable memory as a **user-owned asset**, not something an agent
silently rewrites. New AI-suggested knowledge is classified by a **risk gate**
before it becomes active:

- **Low / medium risk** (most preferences, lessons, project rules) is
  **auto-verified** for next-session use, so the everyday path stays
  low-friction.
- **High risk** (credential values, executable commands, permission or
  MCP-config changes) is routed to **staging** for your review before it
  becomes active.
- **Unsupervised background writeback** is forced to staging regardless of risk,
  and LLM-extracted suggestions **cannot self-label themselves as verified**.

If you want a stricter posture, set `ENGRAM_APPROVAL=strict` and **every** write
— including a caller that tries to pin its own `tier` — is sent to staging for
your approval first.

You stay in control of staged items at any time:

- `review_staging(action="list")` — see what is waiting for review (cold-start
  `get_resume_brief` also surfaces the pending count, including high-risk items).
- Approve, edit, archive, or reject from the review surface.
- Playbooks always require explicit review before trusted use; Engram never
  silently executes a workflow — it hands the steps to your AI tool as a passive
  reference and tracks the reported outcome.

Each entry carries lifecycle metadata (`memory_state`, `approval_status`,
`risk_level`/`risk_flags`, `provenance`, `approval_required`) so the state is
always visible. Full detail and the optional per-caller governance layer
(`ENGRAM_GOVERNANCE=1`, off by default) are documented in the
[Trust model](trust.md) and [Governance](governance.md).

---

## 5. Privacy and data sovereignty

This is the heart of why Engram is local-first.

**What stays local.** By default everything lives under `~/.engram/` (or the
folder you point `ENGRAM_DIR` at) as plain JSON/Markdown: identity, knowledge,
playbooks, project snapshots, recent contexts, and daily logs.

**What never happens by default:**

- No hosted account, no required subscription, no default cloud sync.
- Telemetry is **off**. When you turn on local telemetry it writes a local log
  first; sending anything remote (`engram telemetry remote on`) and weekly
  feedback reports (`engram telemetry feedback on`) are **separate explicit
  opt-ins**. Knowledge content, prompts, AI responses, file paths, emails, and
  IP addresses are never collected.
- Audit logging is **on by default**; it records read/write operations to a
  local `~/.engram/audit.log` (plain JSON-lines, never sent anywhere). Opt out
  with `ENGRAM_AUDIT=0`.
- The per-caller governance layer is **off**; enable it with
  `ENGRAM_GOVERNANCE=1`. This is recommended when the same store is connected
  to multiple AI tools, automation, or remote-facing bridges; `engram status`
  and `engram doctor` show whether it is active.
- `engram setup` does not modify external client configs without your confirm
  (or the explicit `--apply-external-config` flag).

**Your controls:**

- Inspect and edit local JSON/Markdown under `~/.engram/` directly.
- Export a portable identity card with `get_identity_card`.
- Review proposed knowledge before promoting it; archive or update stale items.
- `engram telemetry off` / `engram telemetry preview` to control and inspect
  telemetry payloads.
- Optional field-level encryption for supported sensitive fields with
  `pip install "piia-engram[secure]"` and `ENGRAM_SECRET`.

**Moving or backing up your data:** copy the entire `~/.engram/` folder. That is
your whole memory — there is no cloud copy to reconcile.

**What not to store.** Engram is for personal AI context, not secret management.
Do **not** store passwords, API keys, OAuth tokens, private keys, customer PII,
or regulated data. If a lesson needs sensitive context, store the non-sensitive
reasoning and keep the secret in a real secret manager.

**Honest boundaries.** Engram is a transparent, local-first policy layer — not a
sandbox. Any local process with filesystem access to `~/.engram/` can read your
files; MCP caller identity is self-reported; optional encryption is field-level,
not full-disk. Use OS permissions and disk encryption for stronger isolation.
Full data-flow detail is in [Trust model](trust.md) and
[PRIVACY.md](../PRIVACY.md).

---

## 6. Daily use and maintenance

- **Make the AI remember:** *"Remember this…"* or *"save that as a lesson."*
- **Make the AI recall:** *"What did I say before about…"* or *"follow my usual
  style."*
- **Review the staging queue** periodically (e.g. weekly) with
  `review_staging(action="list")` — especially if you run `ENGRAM_APPROVAL=strict`.
- **Check health** with `engram doctor` (identity completeness, knowledge
  volume, stale items, near-duplicates, decision conflicts, encoding health,
  and a health score). It is local diagnostics — review before sharing.
- **Keep it tidy:** knowledge decays by type (preferences last ~90 days, debug
  tips ~15), and each type is capped so the store does not grow without limit.
  Archive or update stale entries when `doctor` flags them.
- **Optional — upgrade search:** the default keyword search works out of the
  box. If you want cross-lingual recall (an English query finding a Chinese
  note), enable hybrid search: `pip install "piia-engram[vector]"` plus
  `ENGRAM_SEARCH=hybrid`, or take the one-keystroke step in `engram setup`.
  See [hybrid-search.md](hybrid-search.md).

---

## 7. FAQ

**Will Engram upload my data?**
No. Everything is in `~/.engram/`. Telemetry is off by default and, even when
enabled, only ever sends anonymous counts after a separate opt-in — never your
content.

**I switched AI tools — is my memory still there?**
Yes. All tools connected to the Engram MCP read the same local store.

**I said "remember," but the next session didn't know it. Why?**
The AI may have stored it only in its own private memory, not Engram. Verify
with `search_knowledge`; if missing, ask explicitly: *"Use add_lesson to save
that to Engram."*

**Can the AI flood my memory with junk?**
Dedup links or rejects near-identical writes, high-risk content is gated to
staging, and `ENGRAM_APPROVAL=strict` routes *everything* through your review.

**How do I move to a new computer?**
Copy the `~/.engram/` folder. (Multi-machine live sync is not built in yet.)

**Will two tools writing at once corrupt data?**
No. File-level locking serializes concurrent writes.

**How do I know Engram is working in a given tool?**
Run `engram doctor`, or ask the tool to call `get_user_context` /
`get_resume_brief`.

More cross-tool questions are answered in the
[Cross-tool guide FAQ](cross-tool-guide.md#6-faq).

---

## 8. Where to go next

- [Quickstart: first value in ~5 minutes](quickstart-first-value.md)
- [Trust model](trust.md) — data boundaries and what not to store
- [Cross-tool & cross-session guide](cross-tool-guide.md)
- [Governance](governance.md) — the optional per-caller policy layer
- [Telemetry & privacy](telemetry-privacy.md) · [PRIVACY.md](../PRIVACY.md)
- [Honest comparison](honest-comparison.md) — where Engram sits among memory
  databases, repo rule files, and native tool memories
- [Architecture](architecture.md) — how it works inside
