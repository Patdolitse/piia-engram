# piia-engram — Architecture

This document describes how piia-engram is structured internally, why the structure exists, and where each piece lives.

It complements the user-facing [README](../README.md) (which answers *"what does it do"*) by answering *"how is it built and where would I extend it"*.

> **Audience**: contributors, integrators, and anyone reading the code.
> **Version**: v4.0.0 (2026-06-11)

---

## 1. The 30-second mental model

```
┌─────────────────────────────────────────────────────────────────────┐
│  AI tools (Claude Code / Cursor / Codex / Continue / your CLI)      │
└────────────────────────┬────────────────────────────────────────────┘
                         │ stdio  (one MCP process per tool)
                         │   or
                         │ HTTP/SSE  (self-hosted shared instance)
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  mcp_server.py — exposes 54 tools (Tier-1 by default, opt-in rest)  │
└────────────────────────┬────────────────────────────────────────────┘
                         │ Python method calls on a single shared
                         │ ``Engram`` instance
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin) │
│  ── facade in core.py, behavior in mixins ──                        │
│  ReportsMixin = RarityMixin + ReviewMixin + IdentityCardMixin       │
│                 + AnalyticsMixin                                    │
└────────────────────────┬────────────────────────────────────────────┘
                         │ atomic file I/O with portalocker
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ~/.engram/  — local JSON store                                      │
│    identity/        knowledge/        projects/        exports/     │
│    audit.log       schema_version.json                              │
└─────────────────────────────────────────────────────────────────────┘
```

Three layers:

1. **Transport** (`mcp_server.py` + `mcp_tools_*.py`) — thin async wrappers; one per MCP tool. Validates input, calls one method, returns a string.
2. **Domain** (`Engram` class + mixins) — the data model and the rules over it. No I/O of its own beyond the `_read_json` / `_write_json` primitives in `storage.py`.
3. **Storage** — flat JSON files under `~/.engram/`. Atomic writes via temp-file + rename, cross-process locks via `portalocker`.

MCP tool tiering is intentionally conservative: the server defines 54 tools total, with 17 Tier-1 core tools loaded by default and 37 Tier-2 advanced tools behind `ENGRAM_TOOLS=all`.

Alongside the MCP request path there are two **capture channels** that feed the store without the AI having to call a tool: the `hooks/` subpackage (event-driven — the host tool invokes them on session stop/compact/start) and the `watcher/` subpackage (polling fallback for tools without hook support). See [§6](#6-capture-channels-hooks-and-watcher).

The whole thing fits in your laptop's RAM (typical user has < 1 MB on disk) and starts in under 100 ms.

---

## 2. Module map

After the v3.14.1 refactor, the v3.16.0 reports split, and the v3.55.0 monolith split (`setup_wizard` / `mcp_server` / `core` each shed their largest concerns into sibling modules), domain logic is spread across focused modules. The map below covers the load-bearing ones; run `ls src/piia_engram/` for the full list. Two subpackages sit beside them: [`hooks/`](../src/piia_engram/hooks/) and [`watcher/`](../src/piia_engram/watcher/) (see [§6](#6-capture-channels-hooks-and-watcher)).

> **Line counts are approximate**. Run `wc -l src/piia_engram/*.py` to check current values.

### Core modules

| Module | Lines | Responsibility |
|--------|-------|---------------|
| [`storage.py`](../src/piia_engram/storage.py) | ~260 | Constants + I/O primitives (`_read_json`, `_write_json`, `_engram_root`, `_now_iso`) — the only place the rest of the code touches the filesystem |
| [`core.py`](../src/piia_engram/core.py) | ~1573 | `Engram` class facade — `__init__`, schema migration, identity CRUD (profile / preferences / trust_boundaries / quality_standards), lesson/decision add paths, domain & project methods. The v3.55 split moved cross-type ops and playbooks out (below); split-out modules bind back via late `S.<name>` lookups so monkeypatches keep working |
| [`knowledge_ops.py`](../src/piia_engram/knowledge_ops.py) | ~472 | `KnowledgeOpsMixin` — cross-type knowledge operations: update / archive / lifecycle / merge / link (split out of `core.py` in v3.55) |
| [`playbooks.py`](../src/piia_engram/playbooks.py) | ~1974 | `PlaybookMixin` — playbook storage, scoping, management, and execution plans (split out of `core.py` in v3.55) |
| [`tools_registry.py`](../src/piia_engram/tools_registry.py) | ~153 | `ToolRegistryMixin` — local environment tool/program registry (`register_tool` / `find_tool`) |
| [`import_export.py`](../src/piia_engram/import_export.py) | ~920 | `ImportExportMixin` — full-store `export_all` / `import_all`, metadata-only dry-run merge planning, same-key divergent knowledge conflict preview, explicit owner-confirmed version-chain materialization, and local backup migration semantics |
| [`retrieval.py`](../src/piia_engram/retrieval.py) | ~639 | `RetrievalMixin` — tokenization (`_tokenize`, CJK + ASCII + alias expansion), `_bigram_similarity`, `_score_item`, `search_knowledge`, `get_relevant_lessons`, `get_knowledge_inheritance`, `find_similar_knowledge`, bulk add operations, tier promotion (`evaluate_tiers`, `get_staging_summary`), conflict detection (`_detect_decision_conflicts`, `_detect_lesson_conflicts`) |
| [`search_index.py`](../src/piia_engram/search_index.py) | ~461 | Optional hybrid search — rebuildable SQLite index (FTS5 + optional `[vector]` semantic layer, RRF fusion) over the JSON store. JSON stays the single source of truth; enabled via `ENGRAM_SEARCH=hybrid`. See [hybrid-search.md](hybrid-search.md) |
| [`context.py`](../src/piia_engram/context.py) | ~811 | `ContextMixin` — `generate_context` (the cold-start magic), `_estimate_tokens`, ingestion helpers (`_infer_domain`, `ingest_notes`, `extract_session_insights`) + standalone `extract_knowledge` / `ingest_extraction` for LLM-driven extraction |
| [`reconcile.py`](../src/piia_engram/reconcile.py) | ~473 | `ReconcileMixin` — silent import from other AI tools: `reconcile_memories` (scans `~/.claude/projects/*/memory/*.md`), `reconcile_ai_configs` (scans `CLAUDE.md`, `.cursorrules`, `AGENT.md`, etc.) with similarity-based deduplication |
| [`reports.py`](../src/piia_engram/reports.py) | 20 | `ReportsMixin` — thin composition hub, inherits from 4 sub-mixins below |
| [`reports_rarity.py`](../src/piia_engram/reports_rarity.py) | ~84 | `RarityMixin` — `classify_rarity` (WoW-style legendary/epic/rare), `RARITY_TIERS` constant |
| [`reports_review.py`](../src/piia_engram/reports_review.py) | ~517 | `ReviewMixin` — `generate_review_page` (interactive HTML audit), `export_review_page`, `promote_knowledge`, `apply_review` |
| [`reports_identity.py`](../src/piia_engram/reports_identity.py) | ~101 | `IdentityCardMixin` — `export_identity_card` (portable Markdown for non-MCP tools) |
| [`reports_analytics.py`](../src/piia_engram/reports_analytics.py) | ~417 | `AnalyticsMixin` — `get_health_report`, `get_stale_knowledge`, `get_knowledge_digest`, `get_knowledge_overview`, `get_stats`, `export_knowledge_report` |
| [`compat.py`](../src/piia_engram/compat.py) | ~320 | Migration adapters — `migrate_from_oca_memory` (legacy OCA tool), `export_to_openclaw` / `import_from_openclaw` (SOUL.md / MEMORY.md / USER.md format) |

### Supporting modules

| Module | Lines | Responsibility |
|--------|-------|---------------|
| [`mcp_server.py`](../src/piia_engram/mcp_server.py) | ~1400 | FastMCP server core: shared state (`_engram`, `_session`), stdio + SSE transports, `TokenAuthMiddleware`, `_apply_tool_tier` (filters to Tier-1 by default), `_validate_path`, `ToolCallTracker` integration. Re-exports every tool from the `mcp_tools_*` modules |
| `mcp_tools_read / write / knowledge / admin / session .py` | ~330–1030 each | All 54 `@mcp.tool()` async wrappers, grouped by surface (context/recall queries; memory store + playbooks + tool registry; bulk/merge/lifecycle; permissions/governance/import-export; agent-session context). Each binds back to `mcp_server` via late `S.<name>` lookups so module-level state and monkeypatches resolve there |
| [`crypto.py`](../src/piia_engram/crypto.py) | ~166 | `EncryptionEngine` — AES-256-GCM with PBKDF2-SHA256 (600k iterations, v2). Decrypts legacy v1 (100k) for backward compatibility |
| [`telemetry.py`](../src/piia_engram/telemetry.py) | ~337 | `ToolCallTracker` — opt-in anonymous usage statistics (local log first; remote send and weekly feedback are separate, independent opt-ins, count-only/metadata-only), payload validation, HMAC daily ID, preview/status CLI support |
| [`setup_wizard.py`](../src/piia_engram/setup_wizard.py) | ~3049 | `engram setup` wizard + CLI entry — interactive bilingual onboarding with privacy preferences, including the optional one-keystroke hybrid-search step |
| [`doctor.py`](../src/piia_engram/doctor.py) | ~1078 | `engram doctor` — config integrity report + functional checks (split out of `setup_wizard.py`; helpers stay monkeypatchable via late `W.<name>` lookups) |
| [`cli_commands.py`](../src/piia_engram/cli_commands.py) | ~2395 | CLI subcommands (sessions / review / telemetry / backup / dashboard / recall / …), split out of `setup_wizard.py` under the same late-binding contract |
| [`i18n.py`](../src/piia_engram/i18n.py) | ~48 | Shared bilingual text helper (`t()`, language detection) — user-facing strings go through here |
| [`audit.py`](../src/piia_engram/audit.py) | ~54 | `AuditLogger` — default-on local audit trail to `~/.engram/audit.log` (opt out with `ENGRAM_AUDIT=0`) |
| [`stats.py`](../src/piia_engram/stats.py) | ~157 | `piia-engram stats` CLI — GitHub release / PyPI download counters + `--log` snapshot |

### Why this shape?

Before v3.14.1, all of the domain logic lived in a single 4277-line `core.py`. The split was driven by three concrete pressures:

- **Readability**: 4000+ lines is past the point any single contributor can hold in their head; reviewers were rubber-stamping.
- **Test isolation**: importing `core.py` pulled in HTML generation, LLM-extraction prompts, reconcile-loop file globs — making any unit test slow and the dependency graph opaque.
- **Mental model alignment**: contributors think *"I want to change how search ranks results"* — they shouldn't have to navigate around HTML templates to do that.

The mixin pattern was chosen over alternatives because:

| Approach | Pros | Cons |
|----------|------|------|
| **Mixins** (chosen) | Zero API change; methods call each other via `self`; tests already pass | Some IDE introspection limits; mixin order matters for MRO |
| Standalone functions taking `engram` | Cleanest dependency graph | Every method becomes `function(piia-engram, ...)` — breaks all existing call sites |
| Composition (`piia-engram.search.find(...)`) | Reads beautifully | Breaks every existing call site too; needs deprecation period |
| Stay monolithic | No churn | The pressures above keep growing |

We took the lowest-disruption path that solved the immediate readability and test-isolation pressure. The other refactors stay open as future moves.

---

## 3. Data flow — three canonical journeys

### 3.1 Cold start (every new AI session)

```
AI tool boots
   └─▶ calls MCP `get_user_context` (Tier-1)
         └─▶ mcp_server.get_user_context()
               └─▶ Engram.generate_context()   [ContextMixin]
                     ├─▶ get_safe_profile()          [core.py]
                     ├─▶ get_preferences()           [core.py]
                     ├─▶ get_quality_standards()     [core.py]
                     ├─▶ get_relevant_lessons()      [RetrievalMixin]
                     ├─▶ get_decisions()             [core.py]
                     ├─▶ _detect_*_conflicts()       [RetrievalMixin]
                     ├─▶ get_stale_knowledge()       [ReportsMixin]
                     ├─▶ reconcile_memories()        [ReconcileMixin] ← silent side-effect
                     └─▶ reconcile_ai_configs()      [ReconcileMixin] ← silent side-effect
   ◀── returns a Markdown context block (sized to token budget)
AI tool injects it as the first system message
```

The cold start does light **silent reconcile** work — scanning other tools' memory dirs and CLAUDE.md files for items missing from piia-engram, importing them as `staging`-tier lessons (which require user confirmation via the review page before being trusted).

### 3.2 Knowledge capture (during a session)

```
User: "remember that pytest fixtures should be in conftest.py"
AI:   calls MCP `add_lesson(summary="...", domain="python,testing")`
       └─▶ Engram.add_lesson()    [core.py]
             ├─▶ _read_entries(lessons.json)
             ├─▶ _bigram_similarity vs each existing lesson   [RetrievalMixin]
             │     └─ if >= 0.55 → return status="duplicate", abort
             ├─▶ _ensure_fields() — backfill id, timestamp, tier="verified"
             ├─▶ MAX_KNOWLEDGE_ENTRIES eviction (staging items first)
             ├─▶ _write_json — atomic via tempfile + rename + portalocker
             ├─▶ _audit.log("write", "knowledge/lessons", ...)
             └─▶ increment_domain_usage("python"), ("testing")
```

### 3.3 Review and promotion

```
User: reviews staging knowledge
   ├─▶ Terminal path: `engram review`
   │     ├─▶ `engram review show <id>` inspects one item
   │     └─▶ `engram review approve <id> --yes` or
   │         `engram review archive <id> --yes`
   └─▶ Browser path: request_outline_review / export_review_page
         ├─▶ generate_review_page() emits HTML with rarity-colored items
         ├─▶ user confirms or archives staging items
         └─▶ apply_review() → promote_knowledge() × N, archive_knowledge() × N
```

Promotion is the explicit gate: only items the user keeps survive long-term. This is what separates piia-engram from "everything goes into the memory bag" approaches.

---

## 4. Storage layout

Everything lives under `~/.engram/` (override with `ENGRAM_DIR` env var; legacy `~/.piia/` is read if `~/.engram/` doesn't exist yet).

```
~/.engram/
├── schema_version.json     {"schema_version": "2.0", "created_at": "..."}
├── audit.log               JSON-lines, on by default (opt out with ENGRAM_AUDIT=0)
├── identity/
│   ├── profile.json         role, language, technical_level, description, ...
│   ├── preferences.json     work_patterns, communication, tool_preferences
│   ├── work_style.json      (legacy, kept for back-compat reads)
│   ├── trust_boundaries.json default_sharing, restricted_fields, allowed_tools
│   └── quality_standards.json acceptance_threshold, rules, evidence_requirements
├── knowledge/
│   ├── lessons.json         array of {id, summary, detail, domain, tier, access_count, ...}
│   ├── decisions.json       array of {id, question, choice, reasoning, ...}
│   └── domains.json         {domain_name: {project_count, first_seen, last_used}}
├── projects/
│   └── <sha256(folder)>.json per-project snapshot (title, tech_stack, known_issues, current_state, ...)
├── exports/
│   ├── identity_card.md     latest export from export_identity_card
│   ├── review_<date>.html   from export_review_page
│   ├── knowledge_report_<date>.md
│   └── engram_backup_<date>.json
└── compat/                  empty in current schema (reserved for future migrations)
```

### Sensitive fields are encrypted in place

When `ENGRAM_SECRET` is set, fields in `ENCRYPTED_PROFILE_FIELDS` (email, phone, location, company, real_name, address, id_number) are encrypted at rest with AES-256-GCM. Each value is prefixed `enc:v2:` followed by base64(salt + nonce + ciphertext). The salt is per-value, so the same plaintext encrypts to different ciphertexts on disk.

PBKDF2-SHA256 with 600,000 iterations derives the key from `ENGRAM_SECRET` + 16-byte salt. Legacy `enc:v1:` (100k iterations) values continue to decrypt for backward compatibility.

If `ENGRAM_SECRET` is set but the `cryptography` package isn't installed, piia-engram **refuses to start** rather than silently storing plaintext.

### Concurrent writes

Every `_write_json` writes to `<file>.tmp`, fsync's, then `os.replace`s. A `portalocker` file lock on `<dir>/.engram-write.lock` serializes writes from multiple piia-engram processes (typical when multiple AI tools have a stdio MCP each).

---

## 5. The MCP surface

`mcp_server.py` exposes 54 tools. By default (`ENGRAM_TOOLS=core`), only the **Tier-1** subset is registered — these are the tools an AI agent uses in 95% of sessions. Tier-1 is a discoverability and context-budget tier, not a read-only safety class: write, export, and owner/admin behavior is still governed by `TOOL_GOVERNANCE_CLASS`.

| Tier-1 (default) | Why |
|------------------|-----|
| `get_user_context` | Cold-start identity + context |
| `wrap_up_session` | Save insights + sync at session end |
| `memory_store` | Unified write endpoint for lessons, decisions, and playbooks |
| `add_lesson`, `add_decision`, `add_playbook` | Capture knowledge |
| `search_knowledge`, `get_relevant_knowledge`, `get_recall` | Retrieve knowledge and one-call recall bundles |
| `get_identity_card`, `update_identity` | Identity export/write surfaces (`get_identity_card` writes an owner-gated export file) |
| `get_project_context`, `save_project_snapshot` | Per-project state |
| `get_recent_context`, `get_daily_log`, `get_resume_brief` | Recover recent cross-tool work |
| `doctor` | Memory system self-diagnosis |

Set `ENGRAM_TOOLS=all` to expose the full tool surface (review, health, link/unlink, context-governance previews, OpenClaw bridge, bulk operations, etc.) for power users.

### Transport modes

- **stdio** (default) — one piia-engram process per AI tool, isolated FDs, fastest
- **SSE** (`piia-engram serve --transport sse`) — shared HTTP/SSE instance; binds to `127.0.0.1` by default. Binding to `0.0.0.0` emits a stderr warning and requires `--token` (`secrets.compare_digest` check). `ENGRAM_CORS_ORIGINS` env var configures allowed origins.

Startup reconciliation (`reconcile_memories()` + `reconcile_ai_configs()`) is backgrounded by default for MCP startup, so stdio client initialization is not blocked by local AI config scans. `ENGRAM_MCP_STARTUP_SYNC=eager` restores the old synchronous behavior, `ENGRAM_MCP_STARTUP_SYNC=off` skips the startup reconcile pass, and `ENGRAM_EPHEMERAL=1` forces the same skip for container/ephemeral clients. Stdio `auto_migrate()` remains synchronous because stale client config migration must complete before accepting requests. Background reconcile and MCP write tools share a process-local write lock so read-modify-write JSON updates do not overlap during startup.

---

## 6. Capture channels: hooks and watcher

The MCP tools above only fire when the AI decides to call them. Two subpackages capture session context **without** an explicit tool call, so memory accumulates even when the AI forgets to save.

### 6.1 `hooks/` — event-driven capture

Entry points the host tool invokes directly (e.g. `python -m piia_engram.hooks.auto_save_on_stop`), registered into the host's hook config by `engram setup`:

| Hook | Host event | Does |
|------|-----------|------|
| [`auto_save_on_stop.py`](../src/piia_engram/hooks/auto_save_on_stop.py) | Claude Code Stop / SessionEnd | Parses the transcript, saves session metadata via `save_agent_context`; long sessions also `wrap_up_session` + project snapshot |
| [`auto_absorb_compact.py`](../src/piia_engram/hooks/auto_absorb_compact.py) | Claude Code PostCompact | Archives the compaction summary into the per-project daily log (semantic extraction belongs to the companion `agent` hook — not duplicated here) |
| [`auto_inject_resume_brief.py`](../src/piia_engram/hooks/auto_inject_resume_brief.py) | Claude Code SessionStart | Injects `get_resume_brief` output as `additionalContext` |
| `cursor_save_on_stop.py` / `cursor_inject_resume_brief.py` / `cursor_writeback.py` | Cursor equivalents | Same capture/inject pattern; shared plumbing in `_cursor_payload.py`, opt-in writeback gated by `writeback_policy.py` |

**Contract: never block the host.** Every entry point wraps its body in a top-level `except Exception` — a broken Engram install must not break Claude Code or Cursor. Failures are not silent though: each swallow site leaves a one-line breadcrumb via [`_log.py`](../src/piia_engram/hooks/_log.py) in `<ENGRAM_DIR>/logs/hooks.log` (size-capped, never raises). Re-entry is broken via the `CLAUDE_INVOKED_BY=engram_*` guard so a hook-triggered child session cannot loop.

### 6.2 `watcher/` — polling capture (fallback channel)

For tools that have transcripts on disk but no hook system. A scan loop ([`core.py`](../src/piia_engram/watcher/core.py)) polls adapter-discovered transcript files and checkpoints new content via `save_agent_context` (StrictEngram guard: contexts only — the watcher can never write knowledge directly; staging-tier distillation is a separate `ENGRAM_WATCHER_WRITEBACK` opt-in).

- **Adapters** ([`codex_adapter.py`](../src/piia_engram/watcher/codex_adapter.py), [`claude_code_adapter.py`](../src/piia_engram/watcher/claude_code_adapter.py)): `discover(since_days) -> paths` + `parse(path, max_chars, start_offset=0) -> dict`. The claude_code adapter **yields to the Stop hook** — `discover()` returns nothing when `auto_save_on_stop` is wired in the user's Claude settings, so hook users never get duplicate captures.
- **Incremental capture** ([`_segments.py`](../src/piia_engram/watcher/_segments.py)): per-file byte offsets in `watcher_state.json`. Four contracts: a trailing half-written line is never consumed; offset/watermark advance only on successful save (no silent loss); legacy state without offsets migrates from the last recorded size (no re-send on upgrade); offset beyond current file size means rewrite/rotation → full re-read.
- **Ops** ([`install.py`](../src/piia_engram/watcher/install.py)): Windows autostart via Startup `.lnk` + `pythonw.exe`; macOS/Linux currently get cron/systemd instructions only. Errors append to `<ENGRAM_DIR>/logs/watcher.log`.

---

## 7. Conventions and contracts

- **Backward-compatible storage**: any change to JSON shape requires a migration in `_migrate_v1_to_v2`-style methods. `_parse_schema_version` is tuple-based, not string-based (so `"10.0" > "2.0"` is correct).
- **All writes go through `_write_json`** — never write to `~/.engram/` directly. This guarantees atomicity, locking, and (eventually) audit trail consistency.
- **All reads go through `_read_json`** — they tolerate missing/corrupt files and return `{}` or `[]` rather than raising.
- **Constants live in `storage.py`** — adding a new constant means importing it explicitly from one place. No shadow copies.
- **Tests must cover the API surface, not the wrapper** — prefer testing `Engram.add_lesson(...)` to mocking `mcp_server.add_lesson(...)` unless the wrapper itself has logic. `tests/test_mcp_tools.py` is the example for when the wrapper warrants direct tests.

---

## 8. Where to add things

| If you want to add… | Put it in… |
|---------------------|------------|
| A new constant (similarity threshold, field weight, …) | `storage.py` |
| A new search/ranking heuristic | `retrieval.py` (`RetrievalMixin`) |
| A new section in the cold-start context | `context.py` (`ContextMixin.generate_context`) |
| A new external AI tool to reconcile from | `reconcile.py` (`ReconcileMixin._CLAUDE_MEMORY_GLOBS` or `_AI_CONFIG_FILENAMES`) |
| A new report format / dashboard view | `reports.py` (`ReportsMixin`) |
| A new identity field | `core.py` (`_ALLOWED_PROFILE_FIELDS` in `storage.py` + new accessor on `Engram`) |
| A new MCP tool wrapper | The matching `mcp_tools_*.py` module (reference server state via `S.<name>`). Add to `TIER1_TOOLS` in `mcp_server.py` only if it's a 95%-of-sessions tool. |
| Migration from another product's format | `compat.py` |
| A watcher adapter for another tool's transcripts | `watcher/<tool>_adapter.py` (implement `discover` + `parse`, return `end_offset` for incremental capture) + register in `watcher/core.py` `ADAPTERS` |
| A new host-tool hook | `hooks/<name>.py` — never block the host; log swallowed failures via `hooks/_log.py` |
| A new test for an MCP tool | `tests/test_mcp_tools.py` (follow the existing pattern) |

---

## 9. Pointers

- README user-facing intro: [README.md](../README.md) · [中文版](../README.zh-CN.md)
- Hybrid search: [hybrid-search.md](hybrid-search.md) · [中文版](hybrid-search.zh-CN.md)
- Security model: [SECURITY.md](../SECURITY.md)
- Contributing & test baseline: [CONTRIBUTING.md](../CONTRIBUTING.md)
