# Privacy & Data Practices

piia-engram is a **local-first** tool. Your identity, preferences, lessons, and decisions are stored as plain JSON files on your machine. This document describes exactly what data piia-engram handles and how.

## Data Flow Overview

```
┌─────────────────────────────────────────────────────────────┐
│  YOUR MACHINE (~/.engram/)                                  │
│                                                             │
│  identity.json ─┐                                          │
│  lessons.json   ├── Local JSON files (you own these)       │
│  decisions.json ─┘                                          │
│                                                             │
│  ┌──────────────┐    MCP (local stdio)    ┌──────────────┐ │
│  │ Claude Code  │◄──────────────────────►│  piia-engram  │ │
│  │ Cursor       │   (no network)         │  MCP server   │ │
│  │ Codex        │                        └──────┬───────┘ │
│  └──────────────┘                               │          │
│                                                  ▼          │
│                                        telemetry.log       │
│                                        (local, opt-in)     │
└─────────────────────────────────────────────────────────────┘
```

Default implementation: local files only. Telemetry is off by default; when enabled, it writes a local log first. Remote telemetry and weekly feedback reports are separate opt-ins (`engram telemetry remote on`, `engram telemetry feedback on`) and send count-only payloads.

## What piia-engram stores locally

| Data | Location | Purpose |
|------|----------|---------|
| Your profile (name, role, preferences) | `~/.engram/identity/profile.json` | AI tools know who you are |
| Lessons learned | `~/.engram/knowledge/lessons.json` | AI tools remember your experience |
| Key decisions | `~/.engram/knowledge/decisions.json` | AI tools understand your reasoning |
| Playbooks | `~/.engram/playbooks/{id}.json` + `~/.engram/playbooks/_index.json` | Reusable multi-step procedures |
| Project snapshots | `~/.engram/projects/` | Per-project context |
| Session history | `~/.engram/contexts/{tool}/` | Cross-session continuity |

All files are plain JSON. You can open, edit, back up, or delete them at any time.

## Session-end content digest (temporarily withdrawn)

The Claude Code session-end hook can additionally feed a sanitized digest of
the conversation's assistant text into local knowledge extraction. The
implementation is retained for redesign work, but the public preference API
does not accept `hook_content_digest`, so supported installations cannot turn
it on. A closed runtime gate also ignores a literal `true` retained from an
older store or written by hand. The code remains present only for testability
and the planned 4.18 redesign.

If this path is reintroduced after the privacy review, its current controls are:

- only assistant text blocks are read from the local transcript — user
  messages and tool input/output are never collected;
- text is filtered (code fences, quotes, XML envelopes dropped), normalized,
  and scrubbed of credential/path/PII shapes before use, under hard size
  budgets;
- extracted items are staged for your review, never auto-verified;
- the audit trail records category + counts only (no item text);
- every candidate is checked by an output guard before it is stored;
  anything secret-shaped is dropped, not stored.

Residual risks you accept when opting in (phase 1 limits): the filters are
shape-based, not semantic — names, business secrets, or unusual secret
formats without a recognizable shape can pass into staged items; staged items
are included in full local backups/exports you create; and the output guard
is deliberately over-broad, so legitimate content hashes or checksums in a
session may cause some candidate items to be dropped. Review staged items
with `engram review` and delete anything unwanted.

## Network requests

### Default identity and knowledge tools: zero network requests

With default settings, identity, knowledge, search, review, and governance tools operate on local files. They make **no network requests** — no API calls, no analytics, no phone-home.

The only exception is the optional `read_web_content` tool, which fetches a URL you explicitly provide — either through a local sidecar (if you run one) or the self-contained built-in reader. Both paths fetch only the URL you pass in; no other data leaves your machine.

### Optional anonymous usage statistics

piia-engram offers **opt-in** anonymous usage statistics to help the project understand how tools are used. This is:

- **Off by default** — you must explicitly enable it during `engram setup` (Step 5) or via `engram telemetry on`
- **Transparent** — preview the exact payload with `engram telemetry preview`
- **Reversible** — disable anytime with `engram telemetry off`

Local telemetry and remote sending are separate:

- `engram telemetry on` enables local count logging.
- `engram telemetry remote on` enables remote sending of the same count-only telemetry.
- `engram telemetry feedback on` enables weekly anonymous feedback reports.

#### What is collected (when opted in)

| Field | Example | Contains content? |
|-------|---------|-------------------|
| Tool call counts | `{"add_lesson": {"success": 5, "error": 1}}` | No — tool names + counts only |
| Knowledge totals | `{"lessons": 47, "decisions": 12}` | No — counts only |
| Engram version | `"3.42.0"` | No |
| Previous reported version | `"3.41.0"` or `null` | No — version string only |
| Session type | `"first_run"` / `"regular"` | No — first telemetry payload vs later payloads |
| Install-age bucket | `"first_day"`, `"2_7_days"`, `"8_30_days"`, `"31_plus_days"` | No — coarse bucket only, not the exact install time |
| Error category counts | `{"timeout": 1, "validation": 2}` | No — closed categories only, never error text or stack traces |
| Daily anonymous ID | `"a3f8b2c1e9d04f67"` | HMAC-derived, rotates daily, cannot be linked across days |
| OS platform | `"win32"` | No detailed version |
| Python version | `"3.12"` | Major.minor only |

#### What is NEVER collected

- Lesson, decision, or playbook **content** (text, summaries, reasoning)
- User prompts or AI responses
- File paths (may reveal username or project names)
- Error messages, exception text, or stack traces
- IP addresses, email, or device fingerprints
- Domain names or project names

#### Safety mechanisms

- Payload validator rejects any string > 200 characters
- Natural language patterns are detected and rejected
- Local telemetry payloads are human-readable in `~/.engram/telemetry.log`
- `engram telemetry preview` shows the exact next payload before logging or remote sending

### Current status

Telemetry is off by default. If only local telemetry is enabled, data is written to `~/.engram/telemetry.log` and does not leave your machine. Remote telemetry and feedback reports require separate explicit consent and can be disabled with `engram telemetry remote off` and `engram telemetry feedback off`.

### Optional feedback reports

A separate opt-in (`engram telemetry feedback on`, or a manual `engram feedback` after previewing with `engram feedback --dry-run`) sends a weekly aggregated report to help the project understand usage patterns. This uses the same anonymous ID and contains only counts — never content. Rate-limited to once per 7 days.

## Encryption

Optional field-level AES-256-GCM encryption is available for sensitive profile fields:

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="your-strong-passphrase"
```

- PBKDF2 with 600,000 iterations (OWASP 2023+ recommendation)
- Per-value random salt and nonce
- Encrypted fields stored as `enc:v2:...` in JSON files; legacy `enc:v1:...` values still decrypt
- Without `ENGRAM_SECRET`, piia-engram works normally with plaintext

## Access control

- All data is readable by any process with file-system access to `~/.engram/`
- `restricted_fields` filters sensitive profile fields from cold-start context
- Optional agent governance (`ENGRAM_GOVERNANCE=1`) adds self-reported caller trust levels and disclosure receipts; it is not a hardened sandbox or cryptographic caller identity
- Local audit logging is **on by default** — all read/write operations are recorded to `~/.engram/audit.log` (a local file, never sent anywhere); opt out with `ENGRAM_AUDIT=0`

**Recommendation:** Do not store passwords, API keys, or client PII in piia-engram. It is designed for personal AI context, not secrets management.

## Your rights

- **View**: All data is plain JSON — open any file in `~/.engram/`
- **Edit**: Modify any file directly; piia-engram reads on demand
- **Delete**: Remove any file or the entire `~/.engram/` directory
- **Export**: `get_identity_card` generates a portable Markdown summary
- **Disable telemetry**: `engram telemetry off` or set `ENGRAM_TELEMETRY=0`

## Contact

Questions about privacy practices? Open an issue at [github.com/Patdolitse/piia-engram](https://github.com/Patdolitse/piia-engram/issues).
