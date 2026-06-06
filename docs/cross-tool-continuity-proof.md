# Cross-tool continuity proof (live, not simulated)

This is the real version of the [cross-tool continuity demo](cross-tool-continuity-demo.md).

The code demo proves the loop with *simulated* clients against a synthetic store, so anyone can reproduce it with one command. This page records the same loop run with **two genuinely separate AI coding tools** — different vendors, different OS processes, different configs — pointed at **one local store**.

> A memory written by **Claude Code** was read back, unmodified and with its provenance intact, by **Codex** — in a fresh session, through the MCP protocol, with no cloud account and no sync service.

## Claim card

| Field | Value |
|---|---|
| Evidence level | L4 partial cross-client continuity |
| Verified direction | Claude Code writes -> Codex cold-start reads |
| Environment arm | Default-user |
| Evidence type | Live client run, de-identified public summary |
| Not proven | Cursor/Windsurf behavior, proactive recall, every MCP host, broad benchmark superiority, or L5 reproducibility |

## The setup

| | Tool A | Tool B |
|---|---|---|
| Client | Claude Code | Codex |
| Vendor | Anthropic | OpenAI |
| Process | separate | separate |
| MCP server | `piia_engram.mcp_server` | `piia_engram.mcp_server` |
| Store (`ENGRAM_DIR`) | `<shared-store>` | `<shared-store>` |

Both clients run the standard piia-engram MCP server and resolve to the **same local directory**. Nothing leaves the machine. The only thing connecting the two tools is one folder of JSON/Markdown files that the user owns.

## Step 1 — Claude Code writes the handoff

Claude Code recorded one verified lesson after a payment-webhook refactor. The stored record (de-identified):

```json
{
  "id": "a18fcc790545",
  "summary": "Payment webhooks: verify the signature before writing business state, and keep failed events replayable",
  "domain": "payments,backend,webhooks",
  "source_tool": "claude_code",
  "tier": "verified",
  "created_at": "2026-06-06T15:04:03"
}
```

## Step 2 — Codex reads it back in a new session

In a **brand-new Codex session** (no shared context with Claude Code), Codex was asked to retrieve a payment-webhook lesson using the engram `search_knowledge` tool, read-only. Codex returned the same record verbatim, including the provenance stamp showing it was authored by `claude_code`:

```json
{
  "summary": "Payment webhooks: verify the signature before writing business state, and keep failed events replayable",
  "detail": "When handling payment-provider webhooks (Stripe/PayPal-style), verify the request signature before persisting business state ...",
  "domain": "payments,backend,webhooks",
  "source_tool": "claude_code",
  "timestamp": "2026-06-06T15:04:03",
  "created_at": "2026-06-06T15:04:03",
  "last_reviewed": "2026-06-06T15:04:03",
  "id": "a18fcc790545",
  "status": "active",
  "tier": "verified",
  "memory_state": "verified",
  "approval_status": "approved",
  "provenance": {
    "source_tool": "claude_code",
    "created_at": "2026-06-06T15:04:03",
    "entry_type": "lesson",
    "domain": "payments,backend,webhooks"
  }
}
```

Codex's own summary of what it did:

> Read-only retrieval done. No write / modify / add-memory tool was called. The matched lesson, verbatim: …

## Why this is stronger than the code demo

The code demo is honest that it *simulates* Claude Code, Codex, and Cursor inside one Python process against a throwaway store. A skeptic can fairly say "you simulated the tools you claim to bridge."

This proof removes that objection:

- the writer (`claude_code`) and the reader (Codex) are **separate applications from separate vendors**, each with its own MCP client and process;
- the read happened in Codex's **own UI**, not in a script we wrote;
- the only shared state is **one local directory** — no cloud account, no sync server, no shared login;
- the `source_tool: claude_code` provenance survived the handoff intact, so the reader can tell *which tool* contributed the memory.

That is the whole promise in one screenshot: **one memory, written by one AI tool, read by another, owned by you.**

## Reproduce it yourself

You need two MCP-compatible clients and one shared store directory.

1. Install: `pip install piia-engram`.
2. Point both clients' engram MCP server at the same `ENGRAM_DIR` (the default is `~/.engram`; set the env var explicitly if a client uses a different home).
3. In client A, store a lesson (e.g. via `add_lesson` / `memory_store`).
4. In client B, open a fresh session and call `search_knowledge` for it.
5. Confirm the returned record carries `source_tool` = client A.

Prefer a one-command, zero-config version? Run the synthetic [code demo](cross-tool-continuity-demo.md):

```bash
python demos/cross_tool_continuity_demo.py --json
```

## Privacy note

The record above is de-identified. The payment-webhook lesson is generic engineering guidance, not user data. Public materials must follow the same rules as the code demo: no real local paths, usernames, emails, tokens, customer names, or real project names. Engram is not a secrets manager — never store credentials or regulated data in lessons or decisions.
