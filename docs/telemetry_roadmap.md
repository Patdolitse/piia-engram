# Anonymous Usage Statistics — Roadmap

## Guiding Principles

1. **Off by default** — Users must explicitly opt in during `engram setup`
2. **Transparent** — All logged data is visible via `engram telemetry preview` and stored in `~/.engram/telemetry.log`
3. **Minimal** — Only aggregated counts; never content, prompts, file paths, or PII
4. **Reversible** — `engram telemetry off` immediately stops all logging
5. **Local-first narrative** — When disabled, Engram makes zero network requests (except optional `read_web_content`)

---

## Phase 1 (v3.15.0) — Local Log Only

**Status:** Implemented

### What it does
- `engram setup` Step 5 asks users to opt in (default: No)
- When enabled, records daily aggregated data to `~/.engram/telemetry.log`
- **No network requests** — data stays entirely on the user's machine

### Data collected (4 fields)
| Field | Example | Contains content? |
|-------|---------|-------------------|
| Tool call distribution | `{"add_lesson": {"success": 5, "error": 1}}` | No — tool names + counts only |
| Knowledge entry totals | `{"lessons": 47, "decisions": 12, "domains": 3}` | No — counts only |
| Engram version | `"3.15.0"` | No |
| Daily anonymous ID | `"a3f8b2c1e9d04f67"` | HMAC(local_uuid, date) — cannot link across days |

### What is NOT collected (ever, in any phase)
- Lesson/decision content (text, summaries, reasoning)
- User prompts or AI responses
- File paths (may reveal username or project names)
- IP addresses, email, device fingerprint
- OS, Python version, machine name
- Domain names (may reveal profession/client)

### Safety mechanisms
- Payload validator rejects any string > 200 chars or with natural language patterns
- All payloads are human-readable in `telemetry.log`
- `engram telemetry preview` shows exact next payload
- `engram telemetry status` shows current configuration

---

## Phase 2 Decision Gate

**Phase 2 will NOT be built until ALL of the following conditions are met:**

### Prerequisites
1. **Phase 1 has been live for at least 30 days**
2. **At least 5 users have voluntarily shared their `telemetry.log`** (via GitHub Discussions, email, or any channel) — this proves real users exist and care enough to help
3. **No negative community feedback** about Phase 1 (no issues/discussions objecting to the local log)

### If prerequisites are met → Phase 2 (v3.16.0)

- Deploy Cloudflare Worker + D1 as the receiving backend
- Worker code will be **open source** in this repository
- **Must re-consent**: Phase 1 opt-in does NOT carry over; users must explicitly agree to network transmission in a new prompt
- Daily anonymous ID via HMAC (no stable UUID transmitted)
- Worker's first line of code: discard all request metadata (IP, User-Agent, headers)
- Public dashboard on GitHub Pages showing aggregated community data
- `engram telemetry status` will clearly show "Phase 2: data is sent to [URL]"

### If prerequisites are NOT met → Phase 2 is cancelled

If after 30+ days we cannot find 5 users willing to share logs, the CF Worker backend would serve an empty user base. In that case:
- Phase 1 (local log) remains as a useful debugging/self-analysis tool
- The 16 hours of backend development are better spent on user acquisition and onboarding UX

---

## CLI Reference

```bash
engram telemetry status       # Show current opt-in state and config paths
engram telemetry preview      # Show the exact payload that would be logged
engram telemetry on           # Enable anonymous usage statistics
engram telemetry off          # Disable anonymous usage statistics
```

## Environment Variables

| Variable | Effect |
|----------|--------|
| `ENGRAM_TELEMETRY=0` | Force-disable usage statistics (overrides config file) |
| `ENGRAM_TELEMETRY=1` | Force-enable usage statistics (overrides config file) |
| `ENGRAM_RECONCILE=0` | Disable cross-tool memory/config sync |
| `ENGRAM_RECONCILE=1` | Enable cross-tool memory/config sync |

---

## Cross-AI Consultation Record

This design was evaluated by 4 independent AI models (2026-05-22):
- **DeepSeek**: Agreed with CF+D1, suggested adding success/fail counts and P50/P95 latency
- **Claude Opus 4.7**: Agreed technically, questioned timing — recommended Phase 1 as Phase 2 gate
- **Codex 5.5**: Agreed, suggested renaming from "telemetry" to "anonymous usage statistics"
- **ChatGPT Pro**: Agreed, required Phase 2 re-consent and daily-derived ID

All 4 agreed on: opt-in default No, ask once in setup, fix "zero telemetry" docs first, Cline as reference.
Key divergence resolved: Phase 2 is gated by real user participation (Opus 4.7's suggestion).
