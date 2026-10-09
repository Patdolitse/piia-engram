# Hook spool design (4.23.0)

## Inventory before implementation

Latency below is a code-path estimate, not a production measurement. Local filesystem and interpreter startup latency varies by machine.

| Entry point | Reads | Writes / actions today | Latency and failures today |
| --- | --- | --- | --- |
| `scripts/auto_save_on_stop.py` → `hooks.auto_save_on_stop` (Claude Stop / PreCompact) | stdin, whole transcript, opt-in digest preferences, project source/tests | initializes store, checkpoint, `extract_session_insights(force_staging=True)`, quick context, project snapshot | O(transcript + project tree); each store lock can wait 5 s; caught failures lose capture, malformed shape can escape |
| `hooks.auto_absorb_compact` (Claude PostCompact) | stdin, first 10 transcript lines; summary clipped to 3,000 characters | initializes store, appends daily log; semantic extraction is deliberately absent | local reads plus store initialization and up to 5 s lock waits; lost log on exception |
| `scripts/cursor_writeback.py` → `hooks.cursor_writeback` (opt-in Cursor sessionEnd) | stdin summary (20,000 characters) or containment-checked transcript (512 KB tail) | initializes store, staging-only extraction | bounded transcript read plus multiple 5 s write-lock waits; failed extraction discarded |
| `hooks.cursor_save_on_stop` (Cursor stop / sessionEnd) | stdin or Cursor environment fallback, summary (4,000 characters), debounce state | initializes store, appends checkpoint, marks debounce | bounded transcript read plus store init / 5 s lock; repeated stop loops are debounced; failed capture discarded |
| `scripts/auto_inject_resume_brief.py` → `hooks.auto_inject_resume_brief` (Claude SessionStart) | stdin cwd, resume data (1,500 token budget), weekly recap | currently initializes writable store; weekly hint state | no internal wall-clock budget; installer timeout 15 s; store init may block; caught failures continue |
| `hooks.cursor_inject_resume_brief` (Cursor sessionStart) | stdin/env project, resume data, weekly recap | same writable initialization / weekly state | no internal wall-clock budget; catches errors but cannot interrupt a slow call |
| `setup_wizard` hook injection / agent setup | installed module names and synthetic hook commands | client config writes only on authorized setup | installs command hooks; async writes still do store work; no companion agent hook is actually installed (older module comments are stale) |
| `watcher.core._maybe_writeback` / Python staging extraction | saved session summaries | opt-in `force_staging=True` extraction | existing background write path; suitable explicit drain boundary, not a client hook |
| MCP startup / MCP tools / Git maintenance hooks | runtime/config / explicit tool input / repository | existing operations | excluded from automatic draining; startup must never import memory or stage queued events |

## Design

Each store owns `hooks/spool/`. A write hook publishes one UTF-8 JSON line containing schema version, UUID event_id, kind, client, UTC created_at, and a minimal bounded payload (summary or transcript path and required containment context). Publication uses an exclusive temporary file, flush/fsync, and atomic rename to a unique `.jsonl`; concurrent processes never share an append handle. No main-store lock, extraction, project scan or store initialization runs in a producer. Every entry point catches errors and exits 0; diagnostics contain an operation and exception class, never payload text. Store log failure falls back to a sibling local hook log if the store is inaccessible.

The pending cap is 1,000 events / 16 MiB, with 128 KiB per event. Oldest files are moved to `quarantine/` on overflow, under a separate nonblocking spool lock. Publishers retry no locks; if maintenance is busy, the next publisher/drain enforces the cap (a concurrent burst can transiently overshoot). Quarantine is retained for explicit human inspection, counted, and never automatically deleted; its disk usage is outside the pending cap. Temporary files left by interruption are reported separately; never treated as complete events.

An explicit processor drains oldest-first, serialized by a nonblocking spool-only lock. `engram hooks drain [--dry-run] [--json]` runs locally without update checks or telemetry. Dry-run and doctor only stat files, never initialize the store, create locks, quarantine or stage anything. A failed store operation retains the event for the next drain. Invalid schema/JSON/oversize/kind enters quarantine with a reason sidecar. Missing transcript references remain pending for retry; references must outlive the drain and are not snapshots of rewritten transcripts.

Completed receipts contain only event IDs and timestamps; keep them to preserve replay dedup. Replay safety also covers partial processing: candidate rows carry event/operation markers checked under the knowledge lock, and archival checkpoint/log writes use atomic replacement plus event markers. The prepared input is frozen before the first store mutation so partial retry does not extract a changed transcript. All extracted knowledge uses `force_staging=True`, respecting ordinary gates and strict approval; checkpoint/daily archival records keep their existing non-knowledge role.

Automatic draining happens only at existing non-MCP staging extraction boundaries (including enabled watcher writeback). Re-entry is guarded; processing never calls a client, network API, or imports other tools' memories. MCP initialization and MCP-origin calls do not drain and preserve their existing semantics.

SessionStart uses a daemon read worker with a fixed 1 s wall-clock budget covering stdin and store reads; it constructs `Engram(read_only=True)`. Timeout/error returns the existing continue JSON and exit 0. The optional weekly hint is omitted from this deadline path because it writes state. Interpreter/module loading precedes the application budget; stuck operating-system startup and disk failure cannot be made lossless by Python. Diagnostics are best effort when all local paths are unwritable.

## Verification plan

Tests are written before implementation: held store lock and unreadable root, multi-process publication, duplicate and interrupted-replay staging, poison quarantine, offline retry, cap ordering, both SessionStart deadlines, doctor/dry-run immutability, existing staging drain boundary and MCP startup exclusion. Adapt legacy synchronous hook tests to explicitly drain before inspecting delayed output. Related tests during development; full suite once at completion. No network or real client configuration in verification.
