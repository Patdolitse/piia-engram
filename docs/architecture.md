# Engram — Architecture

This document describes how Engram is structured internally, why the structure exists, and where each piece lives.

It complements the user-facing [README](../README.md) (which answers *"what does it do"*) by answering *"how is it built and where would I extend it"*.

> **Audience**: contributors, integrators, and anyone reading the code.
> **Version**: v3.14.2 (2026-05-22)

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
│  mcp_server.py — exposes ~45 tools (Tier-1 by default, opt-in rest)  │
└────────────────────────┬────────────────────────────────────────────┘
                         │ Python method calls on a single shared
                         │ ``Engram`` instance
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin) │
│  ── facade in core.py, behavior in mixins ──                        │
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

1. **Transport** (`mcp_server.py`) — thin async wrappers; one per MCP tool. Validates input, calls one method, returns a string.
2. **Domain** (`Engram` class + mixins) — the data model and the rules over it. No I/O of its own beyond the `_read_json` / `_write_json` primitives in `storage.py`.
3. **Storage** — flat JSON files under `~/.engram/`. Atomic writes via temp-file + rename, cross-process locks via `portalocker`.

The whole thing fits in your laptop's RAM (typical user has < 1 MB on disk) and starts in under 100 ms.

---

## 2. Module map

After the v3.14.1 refactor, the package is split into **7 focused modules + 5 supporting modules**.

### Core modules (post-refactor)

| Module | Lines | Responsibility |
|--------|-------|---------------|
| [`storage.py`](../src/engram_core/storage.py) | 224 | Constants + I/O primitives (`_read_json`, `_write_json`, `_engram_root`, `_now_iso`) — the only place the rest of the code touches the filesystem |
| [`core.py`](../src/engram_core/core.py) | 1083 | `Engram` class facade — `__init__`, schema migration, identity CRUD (profile / preferences / trust_boundaries / quality_standards), knowledge CRUD (add/get/update/archive lessons & decisions), link management, domain & project methods, `export_all` / `import_all` |
| [`retrieval.py`](../src/engram_core/retrieval.py) | 639 | `RetrievalMixin` — tokenization (`_tokenize`, CJK + ASCII + alias expansion), `_bigram_similarity`, `_score_item`, `search_knowledge`, `get_relevant_lessons`, `get_knowledge_inheritance`, `find_similar_knowledge`, bulk add operations, tier promotion (`evaluate_tiers`, `get_staging_summary`), conflict detection (`_detect_decision_conflicts`, `_detect_lesson_conflicts`) |
| [`context.py`](../src/engram_core/context.py) | 688 | `ContextMixin` — `generate_context` (the cold-start magic), `_estimate_tokens`, ingestion helpers (`_infer_domain`, `ingest_notes`, `extract_session_insights`) + standalone `extract_knowledge` / `ingest_extraction` for LLM-driven extraction |
| [`reconcile.py`](../src/engram_core/reconcile.py) | 425 | `ReconcileMixin` — silent import from other AI tools: `reconcile_memories` (scans `~/.claude/projects/*/memory/*.md`), `reconcile_ai_configs` (scans `CLAUDE.md`, `.cursorrules`, `AGENT.md`, etc.) with similarity-based deduplication |
| [`reports.py`](../src/engram_core/reports.py) | 1103 | `ReportsMixin` — `classify_rarity` (WoW-style legendary/epic/rare), `generate_review_page` (interactive HTML audit), `apply_review` (process user's review decisions), `export_identity_card` (portable Markdown for non-MCP tools), `get_health_report`, `get_stale_knowledge`, `get_knowledge_digest`, `get_knowledge_overview`, `get_stats`, `export_knowledge_report` |
| [`compat.py`](../src/engram_core/compat.py) | 318 | Migration adapters — `migrate_from_oca_memory` (legacy OCA tool), `export_to_openclaw` / `import_from_openclaw` (SOUL.md / MEMORY.md / USER.md format) |

### Supporting modules

| Module | Lines | Responsibility |
|--------|-------|---------------|
| [`mcp_server.py`](../src/engram_core/mcp_server.py) | 1300 | FastMCP server: ~45 `@mcp.tool()` async wrappers, stdio + SSE transports, `TokenAuthMiddleware`, `_apply_tool_tier` (filters to Tier-1 by default), `_validate_path` |
| [`crypto.py`](../src/engram_core/crypto.py) | 119 | `EncryptionEngine` — AES-256-GCM with PBKDF2-SHA256 (600k iterations, v2). Decrypts legacy v1 (100k) for backward compatibility |
| [`setup_wizard.py`](../src/engram_core/setup_wizard.py) | 860 | `engram setup` + `engram doctor` CLI — interactive bilingual onboarding |
| [`audit.py`](../src/engram_core/audit.py) | 56 | `AuditLogger` — opt-in audit trail (`ENGRAM_AUDIT=1`) to `~/.engram/audit.log` |
| [`stats.py`](../src/engram_core/stats.py) | 107 | `engram stats` CLI — GitHub release / PyPI download counters |

### Why this shape?

Before v3.14.1, all of the domain logic lived in a single 4277-line `core.py`. The split was driven by three concrete pressures:

- **Readability**: 4000+ lines is past the point any single contributor can hold in their head; reviewers were rubber-stamping.
- **Test isolation**: importing `core.py` pulled in HTML generation, LLM-extraction prompts, reconcile-loop file globs — making any unit test slow and the dependency graph opaque.
- **Mental model alignment**: contributors think *"I want to change how search ranks results"* — they shouldn't have to navigate around HTML templates to do that.

The mixin pattern was chosen over alternatives because:

| Approach | Pros | Cons |
|----------|------|------|
| **Mixins** (chosen) | Zero API change; methods call each other via `self`; tests already pass | Some IDE introspection limits; mixin order matters for MRO |
| Standalone functions taking `engram` | Cleanest dependency graph | Every method becomes `function(engram, ...)` — breaks all existing call sites |
| Composition (`engram.search.find(...)`) | Reads beautifully | Breaks every existing call site too; needs deprecation period |
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

The cold start does light **silent reconcile** work — scanning other tools' memory dirs and CLAUDE.md files for items missing from Engram, importing them as `staging`-tier lessons (which require user confirmation via the review page before being trusted).

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
User: opens browser-based review (engram review)
   └─▶ generate_review_page() emits HTML with rarity-colored items
   └─▶ User unchecks 3 items (archive), keeps 5 staging items (confirm)
   └─▶ Click "Confirm Review" → downloads engram_review_*.json
   └─▶ User: "engram apply_review engram_review_<date>.json"
         └─▶ apply_review() → promote_knowledge() × 5, archive_knowledge() × 3
```

Promotion is the explicit gate: only items the user keeps survive long-term. This is what separates Engram from "everything goes into the memory bag" approaches.

---

## 4. Storage layout

Everything lives under `~/.engram/` (override with `ENGRAM_DIR` env var; legacy `~/.piia/` is read if `~/.engram/` doesn't exist yet).

```
~/.engram/
├── schema_version.json     {"schema_version": "2.0", "created_at": "..."}
├── audit.log               JSON-lines, only written when ENGRAM_AUDIT=1
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
│   └── <sha256(folder)>.json per-project snapshot (title, tech_stack, known_issues, ...)
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

If `ENGRAM_SECRET` is set but the `cryptography` package isn't installed, Engram **refuses to start** rather than silently storing plaintext.

### Concurrent writes

Every `_write_json` writes to `<file>.tmp`, fsync's, then `os.replace`s. A `portalocker` file lock on `<dir>/.engram-write.lock` serializes writes from multiple Engram processes (typical when multiple AI tools have a stdio MCP each).

---

## 5. The MCP surface

`mcp_server.py` exposes ~45 tools. By default (`ENGRAM_TOOLS=core`), only the **Tier-1** subset is registered — these are the tools an AI agent uses in 95% of sessions:

| Tier-1 (default) | Why |
|------------------|-----|
| `get_user_context` | Cold-start identity + context |
| `wrap_up_session` | Save insights + sync at session end |
| `add_lesson`, `add_decision` | Capture knowledge |
| `search_knowledge`, `get_relevant_knowledge` | Retrieve knowledge |
| `get_identity_card`, `update_identity` | Identity reads/writes |
| `get_project_context`, `save_project_snapshot` | Per-project state |

Set `ENGRAM_TOOLS=all` to expose the full ~45 tools (review, health, link/unlink, OpenClaw bridge, bulk operations, etc.) for power users.

### Transport modes

- **stdio** (default) — one Engram process per AI tool, isolated FDs, fastest
- **SSE** (`engram serve --transport sse`) — shared HTTP/SSE instance; binds to `127.0.0.1` by default. Binding to `0.0.0.0` emits a stderr warning and requires `--token` (`secrets.compare_digest` check). `ENGRAM_CORS_ORIGINS` env var configures allowed origins.

---

## 6. Conventions and contracts

- **Backward-compatible storage**: any change to JSON shape requires a migration in `_migrate_v1_to_v2`-style methods. `_parse_schema_version` is tuple-based, not string-based (so `"10.0" > "2.0"` is correct).
- **All writes go through `_write_json`** — never write to `~/.engram/` directly. This guarantees atomicity, locking, and (eventually) audit trail consistency.
- **All reads go through `_read_json`** — they tolerate missing/corrupt files and return `{}` or `[]` rather than raising.
- **Constants live in `storage.py`** — adding a new constant means importing it explicitly from one place. No shadow copies.
- **Tests must cover the API surface, not the wrapper** — prefer testing `Engram.add_lesson(...)` to mocking `mcp_server.add_lesson(...)` unless the wrapper itself has logic. `tests/test_mcp_tools.py` is the example for when the wrapper warrants direct tests.

---

## 7. Where to add things

| If you want to add… | Put it in… |
|---------------------|------------|
| A new constant (similarity threshold, field weight, …) | `storage.py` |
| A new search/ranking heuristic | `retrieval.py` (`RetrievalMixin`) |
| A new section in the cold-start context | `context.py` (`ContextMixin.generate_context`) |
| A new external AI tool to reconcile from | `reconcile.py` (`ReconcileMixin._CLAUDE_MEMORY_GLOBS` or `_AI_CONFIG_FILENAMES`) |
| A new report format / dashboard view | `reports.py` (`ReportsMixin`) |
| A new identity field | `core.py` (`_ALLOWED_PROFILE_FIELDS` in `storage.py` + new accessor on `Engram`) |
| A new MCP tool wrapper | `mcp_server.py`. Add to `TIER1_TOOLS` only if it's a 95%-of-sessions tool. |
| Migration from another product's format | `compat.py` |
| A new test for an MCP tool | `tests/test_mcp_tools.py` (follow the existing pattern) |

---

## 8. Pointers

- README user-facing intro: [README.md](../README.md) · [中文版](../README.zh-CN.md)
- Security model: [SECURITY.md](../SECURITY.md)
- Contributing & test baseline: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Current coverage baseline: [coverage_baseline_v3.14.2.md](coverage_baseline_v3.14.2.md)
- Recent milestone review (v3.13.2 external audit): [milestone_review_v3.13.2.md](milestone_review_v3.13.2.md)
