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

Default implementation: your data stays in local files. Engram sends one anonymous usage ping a day (see [Daily usage ping](#daily-usage-ping-on-by-default)); turn it off with `engram telemetry off` or `DO_NOT_TRACK=1`. Detailed usage statistics are off until you consent and write a local log first. Both interactive setup paths ask one statistics question, default Yes: accepting enables local logging, remote telemetry and weekly feedback together; declining leaves the daily ping preference unchanged. Remote telemetry and weekly feedback reports are separate opt-ins from the daily ping, enabled together by setup Yes or separately through CLI controls (`engram telemetry remote on`, `engram telemetry feedback on`); they send count-only payloads only when an endpoint is configured.

## What piia-engram stores locally

| Data | Location | Purpose |
|------|----------|---------|
| Your profile (name, role, preferences) | `~/.engram/identity/profile.json` | AI tools know who you are |
| Lessons learned | `~/.engram/knowledge/lessons.json` | AI tools remember your experience |
| Key decisions | `~/.engram/knowledge/decisions.json` | AI tools understand your reasoning |
| Lessons and decisions moved out of the active files (storage limits, entries an overwrite import replaced, earlier versions of edited entries) | `~/.engram/knowledge/overflow_archive/{lessons,decisions}.jsonl` | Nothing is dropped; append-only, encrypted like the active files when encryption is on, not pruned automatically, included in `export_all` (earlier versions excepted); `engram retention plan` shows what is there |
| Playbooks | `~/.engram/playbooks/{id}.json` + `~/.engram/playbooks/_index.json` | Reusable multi-step procedures |
| Project snapshots | `~/.engram/projects/` | Per-project context |
| Session history | `~/.engram/contexts/{tool}/` | Cross-session continuity |

All files are plain JSON (the overflow archive is JSON Lines). You can open, edit, back up, or delete them at any time.

## Session-end content digest (opt-in, default off)

The Claude Code session-end hook can feed a sanitized digest of the
conversation's assistant text into local knowledge extraction. This is
**off by default** and only activates when the `hook_content_digest_v2`
preference is set to the literal boolean `true` (via
`update_preferences`/`update_identity`; strings, numbers, and the legacy
`hook_content_digest` boolean key are all ignored — a persisted `true`
from an older version is inert by construction).

When enabled:

- only assistant text blocks are read from the local transcript — user
  messages and tool input/output are never collected;
- text is filtered (code fences, quotes, XML envelopes dropped), normalized
  (NFKC + zero-width folding BEFORE detection), and scrubbed of
  credential/path/PII shapes, under hard size budgets;
- cross-line secret pairs (key-form line + value line) are detected after
  normalization, so zero-width or homoglyph obfuscation of the key form
  cannot bypass the pairing;
- the final whole-digest rescan never returns the raw digest when any
  redaction fired;
- extracted items are staged for your review, never auto-verified;
- the audit trail records category + counts only (no item text);
- every candidate is checked by an output guard (including a
  prev+current sentence window for cross-line pairs) before it is
  stored; anything secret-shaped is dropped, not stored.

Residual risks you accept when opting in: the filters are shape-based,
not semantic — names, business secrets, or unusual secret formats without
a recognizable shape can pass into staged items; natural-language
paraphrases of a secret cannot be caught by finite regular expressions
and are an explicitly accepted residual risk; staged items are included
in full local backups/exports you create; and the output guard is
deliberately over-broad, so legitimate content hashes or checksums in a
session may cause some candidate items to be dropped. Review staged items
with `engram review` and delete anything unwanted.

## Network requests

### Default identity and knowledge tools: zero network requests

With default settings, identity, knowledge, search, review, and governance tools operate on local files. They make **no network requests** of their own — no API calls, no analytics. Separately, Engram sends the daily usage ping described below.

Apart from that ping, the only exception is the optional `read_web_content` tool, which fetches a URL you explicitly provide — either through a local sidecar (if you run one) or the self-contained built-in reader. Both paths fetch only the URL you pass in; no other data leaves your machine.

### Daily usage ping (on by default)

Engram sends one anonymous usage ping a day so the project knows how many installs are active. It contains a random install ID (created on your machine, stored in a `piia-engram` folder under your user config directory; `engram telemetry reset-id` makes a new one), the Engram version, OS family, Python major.minor version, the AI client name and the date. It never contains memories, file paths, account details, command arguments or error text, and the server does not store IP addresses; pings are kept for 400 days.

- **Off when:** `engram telemetry off` (or `engram telemetry remote off`), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`; in CI and in containers; or if you explicitly turned the detailed statistics or their remote sending off before 4.23.0 (including a "no" in an earlier `engram setup`). From 4.23.0, setup answers control only detailed statistics and preserve the daily ping preference, including earlier opt-outs. `engram telemetry on` turns it back on (and also turns local statistics back on).
- **Transparent:** `engram telemetry status` shows whether it is on and why; `engram telemetry preview` prints the exact payload.
- **Endpoint:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently.

### Update check

The `engram` command checks PyPI for a newer version at most once a day, only in interactive terminals and never in CI; `engram doctor` also checks on each run. The request carries no data about you. Turn it off with `ENGRAM_NO_UPDATE_CHECK=1`.

### Optional detailed usage statistics

piia-engram offers **opt-in** anonymous usage statistics to help the project understand how tools are used. This is:

- **Off by default until consent** — `engram setup` and `engram setup --advanced` ask the same single statistics question with default Yes; Yes (including accepting the default) enables local and remote statistics plus weekly feedback, and No disables them without changing the daily ping preference. Non-interactive setup does not change these choices. `engram telemetry on` enables local statistics separately.
- **Transparent** — preview the exact payload with `engram telemetry preview`
- **Reversible** — disable anytime with `engram telemetry off`

Local telemetry, remote sending and weekly feedback can also be controlled separately through the CLI:

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

The daily usage ping is on by default (turn it off as described above). Detailed usage statistics stay off until consent through the shared setup question or the CLI. If only local telemetry is enabled, data is written to `~/.engram/telemetry.log` and does not leave your machine. Setup Yes enables local logging, remote telemetry and weekly feedback together; CLI controls can enable them separately. Remote sends require a configured endpoint. Remote telemetry and feedback reports can be disabled with `engram telemetry remote off` and `engram telemetry feedback off`.

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
