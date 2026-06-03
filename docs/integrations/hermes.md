# Using Engram with Hermes (via MCP)

> Version: 3.48.0+ | Updated: 2026-06-03
> **Status: Path B (MCP) only.** The config schema below is **verified against Hermes source**
> (`skills/mcp/native-mcp/SKILL.md`, `agent/memory_provider.py`, branch `main`, 2026-06-03).
> The **end-to-end path was verified locally on 2026-06-03** against `hermes-agent` 0.15.2 (CLI, isolated env):
> Hermes connected to the Engram MCP server over stdio and the agent (DeepSeek `v4-flash`) autonomously called a
> read tool and recited accurate identity. **Scope is bounded** — see [§6](#6-manual-verification-checklist) for
> exactly what was and was not checked, and re-verify on your own Hermes version/surface before citing it publicly.
> Engram is **not** a Hermes memory provider and does **not** occupy Hermes's single external
> memory-provider slot. This is **not** an official Nous Research integration or endorsement —
> it uses Hermes's public MCP client support.

Hermes (Nous Research) is a full **MCP client**. Engram ships an **MCP server** (`piia-engram-mcp`).
That means Hermes can read your local Engram identity through a standard protocol — no cloud, no
account, no provider lock-in. Your data stays in `~/.engram`. Hermes is **one more consumer** of an
identity you own; the same files already feed Claude Code, Codex, and Cursor.

---

## Table of Contents

1. [What this is (and is not)](#1-what-this-is-and-is-not)
2. [Prerequisites](#2-prerequisites)
3. [Configure Hermes to use Engram](#3-configure-hermes-to-use-engram)
4. [What Hermes can query](#4-what-hermes-can-query)
5. [Data ownership & privacy](#5-data-ownership--privacy)
6. [Manual verification checklist](#6-manual-verification-checklist)
7. [What this guide does NOT cover](#7-what-this-guide-does-not-cover)
8. [FAQ](#8-faq)

---

## 1. What this is (and is not)

- **IS:** Engram exposed to Hermes as a standard MCP server, so Hermes can read/query your identity,
  preferences, lessons, and decisions at runtime.
- **IS NOT:** a Hermes memory-provider plugin. Engram does **not** take Hermes's single external-provider
  slot, so it does **not** compete with or replace Mem0 / Honcho / Hindsight.
- **IS NOT:** an official integration or endorsement by Nous Research. It relies only on Hermes's public
  MCP client support.

| Path | What it is | Status |
|---|---|---|
| **B — Engram as MCP server** (this guide) | Hermes queries Engram live over MCP; data stays local | Zero new code; **verified locally 2026-06-03** (hermes-agent 0.15.2, CLI) |
| A — export an OpenClaw migration seed | A one-time snapshot for `hermes claw migrate` | **Not shipped** — see [§7](#7-what-this-guide-does-not-cover) |
| C — Engram as a Hermes memory-provider plugin | Engram becomes Hermes's external memory provider | **Intentionally not built** — see [§7](#7-what-this-guide-does-not-cover) |

---

## 2. Prerequisites

- Engram available, either installed (`pip install piia-engram`) or runnable via `uvx` without installing.
- A working Hermes install with MCP client support and access to its config file (`~/.hermes/config.yaml`).
- **Note on Hermes desktop:** as of 2026-06-03 the Hermes desktop app is a **soft launch / preview**, not a
  confirmed GA release — an installer is served from `hermes-assets.nousresearch.com` (Last-Modified 2026-06-02),
  while one official page still reads "Coming Soon" and the GitHub releases ship only Python artifacts. This guide
  targets the **MCP client configuration**, which is shared across Hermes surfaces (CLI / desktop) — confirm the
  exact config path against the Hermes version you have installed (see [§6](#6-manual-verification-checklist)).

---

## 3. Configure Hermes to use Engram

Add Engram under the `mcp_servers` key in `~/.hermes/config.yaml`.

**If Engram is installed:**

```yaml
mcp_servers:
  piia-engram:
    command: piia-engram-mcp
```

**Or run it without installing, via `uvx`:**

```yaml
mcp_servers:
  piia-engram:
    command: uvx
    args: ["--from", "piia-engram", "piia-engram-mcp"]
```

No `transport` field is needed: Hermes infers **stdio** from `command` (it reserves `url` for HTTP servers), and
`piia-engram-mcp` itself defaults to stdio. Each entry also accepts optional `args` (list), `env` (map), `timeout`
(default 120s), and `connect_timeout` (default 60s). Schema source: Hermes `skills/mcp/native-mcp/SKILL.md` and
`agent/memory_provider.py` (branch `main`, 2026-06-03).

---

## 4. What Hermes can query

Once connected, Hermes can call Engram's MCP tools, for example:

| Tool | Returns |
|---|---|
| `get_identity_card` | Your role, preferences, tech stack |
| `get_user_context` | Identity + recent lessons/decisions |
| `search_knowledge` | Keyword search over lessons / decisions / playbooks |
| `get_relevant_knowledge` | Project-scoped recall |

This is **read/query** access to an identity you own. Any **writes** remain governed by Engram's
`staging → verified` approval gate: an agent can *propose* knowledge, but it enters long-term memory only
after **you** approve it.

---

## 5. Data ownership & privacy

- All data stays in your local `~/.engram` directory. Nothing is uploaded; no account is required.
- Hermes reads **through MCP**; it does not receive a copy of your store.
- Engram's governance still applies end-to-end: **AI can propose. You approve. Engram remembers.**
- The same `~/.engram` identity is portable across tools — switching or adding agents does not lose it.

---

## 6. Manual verification checklist

**Verified locally on 2026-06-03** against `hermes-agent` 0.15.2 (CLI) with DeepSeek `v4-flash` as the agent
model, in an isolated environment (separate venv + `HERMES_HOME` redirect, so the real `~/.hermes` was untouched).
The boxes below record **exactly** what was checked — three are deliberately left open because they were not
exercised. **Re-verify on your own Hermes version/surface** (especially the desktop GUI, which was *not* tested here).

- [x] Schema verified against branch `main` 2026-06-03; `mcp_servers` key name and config path match
      (`hermes config path` resolved to the configured file)
- [x] Hermes installed **with the `[mcp]` extra** — MCP support is an optional dependency, *not* in the base
      install (`pip install hermes-agent[mcp]`); config updated with the snippet from [§3](#3-configure-hermes-to-use-engram)
- [x] `hermes mcp test <name>` reports **✓ Connected** over stdio and discovers Engram's tools (note: the
      `tools.include` whitelist narrows what the *agent* may call; `mcp test` still lists every tool the server exposes)
- [x] Hermes calls a read tool and returns accurate identity — verified via **`get_user_context`** in a
      non-interactive run (`hermes -z "…" --yolo`); the agent recited real preferences sourced from `~/.engram`
- [ ] `get_identity_card` / `search_knowledge` invoked directly and return results — *not separately exercised*
      (both are whitelisted and were discovered, but only `get_user_context` was actually triggered)
- [x] `~/.engram` data is unchanged afterward; no cloud account was prompted
- [ ] No private/restricted fields leaked beyond what governance permits — *spot-checked only, not a systematic
      audit*: the returned fields were identity / preferences / experience-counts, nothing obviously over-scoped
- [ ] Verified on **your** Hermes surface — the run above was CLI only; the **desktop GUI MCP panel was not tested**

---

## 7. What this guide does NOT cover

### Path A — OpenClaw migration seed (not shipped)

Hermes's `hermes claw migrate` reads OpenClaw-format `SOUL.md` / `MEMORY.md` / `USER.md` from
`<openclaw_root>/workspace/` (default `~/.openclaw/workspace/`). Engram **already** ships a **neutral**
`export_to_openclaw(engram, output_dir)` that writes exactly those three files — so Path A is mostly a matter of
pointing that existing export at the workspace directory, not new Hermes-specific code. Two gaps remain before it
is a documented, tested seed flow: (1) **size budget** — Hermes injects `MEMORY.md` as a small frozen snapshot, so
the current "up to 50 lessons + 30 decisions" dump can overflow and needs trimming; (2) **format** — the export
emits Markdown headings/bullets, which `claw migrate` reads via its fallback parser rather than a strict schema, so
round-trip fidelity should be verified. Both fixes belong **inside the neutral `export_to_openclaw`**, not a new
`export_for_hermes`.

We deliberately will **not** ship a Hermes-specific `export_for_hermes`: the neutral OpenClaw export is the
right primitive, and a seed is a *one-time snapshot* whose real value is to funnel you into the **live MCP
path** above. If/when Path A ships, it will be a thin, clearly-labeled "static snapshot, not live memory"
helper — not a core feature.

### Path C — Hermes memory-provider plugin (intentionally not built)

Hermes runs **only one external memory provider at a time**. Becoming that provider would mean competing with
Mem0 / Honcho for a single slot **and** re-framing Engram as "Hermes's memory." Engram is a **neutral,
cross-tool identity layer** — so we deliberately do not do this.

Reconsider only if **all** of these become true: the Hermes community explicitly asks for it; it can coexist
with other providers; and it is scoped as a **read-only identity provider**, not a full-memory takeover.

---

## 8. FAQ

**Q: Does this make Engram "Hermes's memory"?**
No. Engram is exposed as a standard MCP server. Hermes is one consumer; the same `~/.engram` already feeds
Claude Code, Codex, and Cursor.

**Q: Does it compete with Mem0 / Honcho?**
No. Those plug into Hermes's single external memory-provider slot. Engram via MCP does not use that slot.

**Q: Is this an official Nous Research integration?**
No. It uses Hermes's public MCP client support. No endorsement is implied.

**Q: Where is my data?**
In `~/.engram`, on your machine. Hermes queries it live over MCP; it does not copy it to any cloud.
