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

By default you get **18 core MCP tools** (`ENGRAM_TOOLS=core`) — enough for
install, first value, daily recall, and session wrap-up. The advanced set
(review queues, import/export, governance, migration, Playbook management) stays
off until you opt in with `ENGRAM_TOOLS=all`.

Engram does not read your other AI tools' files on its own. To bring in what
they already know (memory files, `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, …),
run `engram import-memories`: it lists the items first, then adds them to the
review queue after you confirm (`engram review` to approve them).

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
reconciliation by default. `run_reconcile=True` is still accepted but imports
nothing; memories from other AI tools come in only through
`engram import-memories`.

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
- In a terminal, `engram review interactive` (or `engram review -i`) shows one
  pending proposal at a time (type, text, risk, where it came from, a possible
  duplicate with its diff, what it replaces) and takes one letter plus Enter:
  `a` approve, `r` reject (optional reason, kept in the receipt only and not returned by
  `get_audit_log` over MCP), `s` supersede an approved entry
  (you type its id), `k` skip, `v` full text, `q` stop. Nothing is written until
  you confirm the summary with `y`; `n`, end of input or Ctrl+C write nothing.
  It applies through the same path as `engram review apply` and leaves the same
  receipt. Without a terminal, use `engram review export --out <dir>` and
  `engram review apply <marks.json>`.
- The export writes `review.md` (one card per proposal, with its version),
  `ids.json` (the ids) and `marks-template.json`, one entry per proposal to fill
  in and save as `marks.json`:
  `{"id": "...", "mark": "approve", "expected_version": 2}`. `mark` is
  `approve`, `reject`, `edit-type:<type>`, `supersede:<id>` (approve it as the
  replacement of the approved entry `<id>`, same kind and scope), `retire`,
  `restore` or `skip` (leave it pending). Optional fields: `reason` on a reject
  (your note, kept in that run's receipt only, never on the rejection record)
  and `expected_version` (approve, reject and supersede only; the item is
  skipped if it changed since). An id takes one of approve / reject /
  supersede / skip, and a run may replace each entry once (otherwise the file
  is refused before anything is written). Order: plain approvals and
  rejections; then the marks that replace an entry; then edit-type; then retire
  / restore. So you can approve an entry and its replacement in one run, and a
  target you reject fails only the mark that names it. List a chain of
  replacements from oldest to newest; a replacement whose target is approved in
  the same run is applied after it anyway (the interactive review relies on
  this). The "same type" check of a supersede uses the `type:` labels both
  entries have after the run's edit-type marks, so relabeling a proposal to its
  target's type in the same file works and relabeling it to another type is
  refused. edit-type on an archived playbook (or one its replacement archives in
  the same run) is skipped, not failed. The dry run follows the same order and
  shows what the applying run will do; each receipt item names its phase and the receipt lists the ids in
  the order they were applied. `engram review apply` exits non-zero when every
  mark in the file failed.
- Playbooks always require explicit review before trusted use; Engram never
  silently executes a workflow — it hands the steps to your AI tool as a passive
  reference and tracks the reported outcome.

What your AI receives follows the same rule everywhere:

- Context it gets without asking (cold start, the resume brief, the
  session-start hooks, `get_recall`, `get_relevant_knowledge`) holds reviewed,
  current items only. Items waiting for review, items replaced by a newer
  version and archived items are left out. Recall only trusts items that are
  clearly marked reviewed: an unknown tier, a missing status or a rejected or
  deprecated label keeps an item out.
- `search_knowledge` lists items waiting for review in a separate `pending`
  group (each marked `pending_untrusted`), never mixed into the results.
  Replaced items are left out unless you pass `include_superseded=true`.
  A `{"tier": "archived"}` filter returns nothing, and `engram dock-search`
  shows at most `--limit` items per kind in total.
- Reading one item by id (`get_knowledge_history`, `explore_knowledge`) still
  returns a replaced item and names the item that replaced it (`superseded_by`).
- When a token budget cuts content, the response says what was left out
  (`omitted`: count, ids and section names), and text context ends with one
  line such as `已省略 3 项（预算）：lessons, decisions` (cold start) or
  `Omitted 3 items (budget): lessons, decisions` (resume brief and hooks). `engram preview` shows
  the trimmed items' summaries.

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

**Defaults:**

- No hosted account, no required subscription, no default cloud sync.
- Engram sends one anonymous usage ping a day (random install ID, version, OS,
  Python version, AI client name, date). Turn it off with `engram telemetry off`,
  `ENGRAM_TELEMETRY=0` or `DO_NOT_TRACK=1`; it is off in CI and in containers.
- Detailed usage statistics stay off unless you turn them on and write a local
  log first; remote sending (`engram telemetry remote on`) and weekly feedback
  reports (`engram telemetry feedback on`) are **separate explicit opt-ins**.
  Knowledge content, prompts, AI responses, file paths, emails, and IP addresses
  are never collected.
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
  `review_staging(action="list")` — especially if you run `ENGRAM_APPROVAL=strict`;
  decide it in a terminal with `engram review interactive`.
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
No. Your memories stay in `~/.engram/`. Engram sends one anonymous usage ping a
day (random install ID, version, OS, Python version, AI client name, date); turn
it off with `engram telemetry off`, `ENGRAM_TELEMETRY=0` or `DO_NOT_TRACK=1`.
Detailed usage statistics stay off unless you turn them on and, even then, only
ever send anonymous counts after a separate opt-in — never your content.

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
