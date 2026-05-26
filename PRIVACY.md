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
                                                  │
                              Phase 2 only        │ opt-in
                              (not yet active)    │ anonymous
                                                  ▼
                                    ┌──────────────────────┐
                                    │  Cloudflare Worker   │
                                    │  (aggregated counts  │
                                    │   only, no content)  │
                                    └──────────────────────┘
```

## What piia-engram stores locally

| Data | Location | Purpose |
|------|----------|---------|
| Your profile (name, role, preferences) | `~/.engram/identity.json` | AI tools know who you are |
| Lessons learned | `~/.engram/lessons.json` | AI tools remember your experience |
| Key decisions | `~/.engram/decisions.json` | AI tools understand your reasoning |
| Playbooks | `~/.engram/playbooks.json` | Reusable multi-step procedures |
| Project snapshots | `~/.engram/projects/` | Per-project context |
| Session history | `~/.engram/sessions/` | Cross-session continuity |

All files are plain JSON. You can open, edit, back up, or delete them at any time.

## Network requests

### Core tools: zero network requests

All 61 MCP tools (identity, knowledge, search, review, etc.) operate entirely on local files. They make **no network requests** — no API calls, no analytics, no phone-home.

The only exception is the optional `read_web_content` tool, which fetches a URL you explicitly provide.

### Optional anonymous usage statistics

piia-engram offers **opt-in** anonymous usage statistics to help the project understand how tools are used. This is:

- **Off by default** — you must explicitly enable it during `engram setup` (Step 5) or via `engram telemetry on`
- **Transparent** — preview the exact payload with `engram telemetry preview`
- **Reversible** — disable anytime with `engram telemetry off`

#### What is collected (when opted in)

| Field | Example | Contains content? |
|-------|---------|-------------------|
| Tool call counts | `{"add_lesson": {"success": 5, "error": 1}}` | No — tool names + counts only |
| Knowledge totals | `{"lessons": 47, "decisions": 12}` | No — counts only |
| Engram version | `"3.29.1"` | No |
| Daily anonymous ID | `"a3f8b2c1e9d04f67"` | HMAC-derived, rotates daily, cannot be linked across days |
| OS platform | `"win32"` | No detailed version |
| Python version | `"3.12"` | Major.minor only |

#### What is NEVER collected

- Lesson, decision, or playbook **content** (text, summaries, reasoning)
- User prompts or AI responses
- File paths (may reveal username or project names)
- IP addresses, email, or device fingerprints
- Domain names or project names

#### Safety mechanisms

- Payload validator rejects any string > 200 characters
- Natural language patterns are detected and rejected
- All payloads are human-readable in `~/.engram/telemetry.log`
- `engram telemetry preview` shows the exact next payload before sending

### Phased rollout

| Phase | Status | Network? | Details |
|-------|--------|----------|---------|
| **Phase 1** (v3.15.0+) | Active | No — local log only | Data written to `~/.engram/telemetry.log`, stays on your machine |
| **Phase 2** | Not yet active | Yes — opt-in remote | Requires separate re-consent; will not activate until prerequisites are met (see [telemetry_roadmap.md](docs/telemetry_roadmap.md)) |

Phase 2 prerequisites (all must be met before it can be built):
1. Phase 1 live for 30+ days
2. At least 5 users voluntarily share their telemetry.log
3. No negative community feedback about Phase 1

If prerequisites are not met, Phase 2 is cancelled. See [telemetry_roadmap.md](docs/telemetry_roadmap.md) for full details.

### Optional feedback reports

A separate opt-in (`engram feedback`) sends a weekly aggregated report to help the project understand usage patterns. This uses the same anonymous ID and contains only counts — never content. Rate-limited to once per 7 days.

## Encryption

Optional field-level AES-256-GCM encryption is available for sensitive profile fields:

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="your-strong-passphrase"
```

- PBKDF2 with 600,000 iterations (OWASP 2023+ recommendation)
- Per-value random salt and nonce
- Encrypted fields stored as `enc:v1:...` in JSON files
- Without `ENGRAM_SECRET`, piia-engram works normally with plaintext

## Access control

- All data is readable by any process with file-system access to `~/.engram/`
- `restricted_fields` filters sensitive profile fields from cold-start context
- MCP protocol does not currently support caller identity, so per-tool ACL is not possible
- Optional audit logging (`ENGRAM_AUDIT=1`) tracks all read/write operations to `~/.engram/audit.log`

**Recommendation:** Do not store passwords, API keys, or client PII in piia-engram. It is designed for personal AI context, not secrets management.

## Your rights

- **View**: All data is plain JSON — open any file in `~/.engram/`
- **Edit**: Modify any file directly; piia-engram reads on demand
- **Delete**: Remove any file or the entire `~/.engram/` directory
- **Export**: `get_identity_card` generates a portable Markdown summary
- **Disable telemetry**: `engram telemetry off` or set `ENGRAM_TELEMETRY=0`

## Contact

Questions about privacy practices? Open an issue at [github.com/Patdolitse/piia-engram](https://github.com/Patdolitse/piia-engram/issues).
