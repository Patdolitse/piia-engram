# Changelog

All notable changes to Engram are documented in this file. For detailed release notes with upgrade instructions, see [GitHub Releases](https://github.com/Patdolitse/piia-engram/releases).

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

## [3.28.1] - 2026-05-24

Telemetry default-on and auto project snapshots.

### Added
- Telemetry defaults to enabled for new installs (no setup wizard needed)
- Auto project snapshot on MCP server exit — collects version, module count, test count, MCP tool count
- `_collect_project_info()` helper for filesystem-based project metrics
- Stop Hook (`auto_save_on_stop.py`) also updates project snapshots

### Fixed
- Test isolation: `isolated_engram` fixture now resets `_session` to prevent atexit data leak to real `~/.engram/`

## [3.28.0] - 2026-05-23

Session auto-tracking and execution plan fix.

### Added
- MCP server session auto-tracking via `_SessionTracker` — records all tool calls during session
- `atexit` auto-save: persists session context on MCP server shutdown (tool list, call count, duration)
- Claude Code Stop Hook script (`scripts/auto_save_on_stop.py`) — saves session metadata when conversation ends
- Three-layer session protection: AI manual save (high quality) → MCP atexit (medium) → Stop Hook (basic)

### Fixed
- `prepare_playbook_execution` now auto-saves execution plan in core layer (previously only saved at MCP layer, causing data loss when called via Python API)
- Removed redundant `save_execution_plan` call from MCP layer (now handled by core)

## [3.27.1] - 2026-05-23

### Fixed
- Telemetry opt-in now part of normal setup wizard flow, not hidden behind `--advanced`
- Identity card content quality: limit domains, filter config directives, clean XML artifacts

## [3.27.0] - 2026-05-23

Execution tracking, stats i18n, and steps format compatibility.

### Added
- Playbook execution tracking: `prepare_playbook_execution` → `update_execution_step` → `get_execution_status`
- i18n module with `t(zh, en)` for bilingual output in stats

### Fixed
- Handle string-format steps in playbook parameter extraction, merge, and execution

## [3.26.0] - 2026-05-23

Playbook lifecycle, bilingual UX, knowledge intelligence.

### Added
- Playbook auto-extraction improvements
- Tools registry as Tier-1 knowledge type

## [3.25.0] - 2026-05-23

### Changed
- Playbook auto-extraction P0 improvements
- Bumped MCP Registry server.json version

## [3.24.0] - 2026-05-23

Phase 2 remote telemetry with Cloudflare Worker dashboard.

### Added
- Opt-in remote anonymous usage statistics via Cloudflare Worker + D1
- Visual telemetry dashboard (Chinese, password-protected) with PyPI download stats
- Periodic telemetry flush (every 10 tool calls) to prevent data loss on exit
- `atexit` handler as fallback flush on MCP server shutdown
- `force` parameter on `ToolCallTracker.flush()` to bypass daily rate limit
- Remote consent: `engram telemetry remote on/off/status` CLI commands
- 18 new telemetry tests (remote config, sender, payload fields)

### Changed
- `wrap_up_session` now force-flushes telemetry (previously skipped if already flushed today)
- Telemetry payload includes `os_platform`, `python_version`, `tools_tier` fields

## [3.23.0] - 2026-05-23

New knowledge type: **Playbook** — structured operational procedures stored as individual files for future sharing.

### Added
- Playbook knowledge type: multi-step operational procedures with trigger keywords
- Independent file storage (`~/.engram/playbooks/<id>.json`) with lightweight index
- Trigger-based retrieval: keyword anchors for instant recall (e.g., search "发布 registry" to find publish workflow)
- MCP tools: `add_playbook` (Tier-1), `get_playbooks`, `get_playbook`
- `search_knowledge` extended with `scope="playbooks"` support
- Trigger exact-match scoring bonus (weight 5.0 per hit) for high-precision retrieval
- Playbook support in `export_all` / `import_all` for backup and migration
- Playbook support in `update_knowledge` / `archive_knowledge` / `_find_item_by_id`
- Playbook tier promotion in `evaluate_tiers`
- 15 new tests covering full playbook lifecycle

### Changed
- `FIELD_WEIGHTS` extended with `triggers` (4.0) and `description` (2.0)
- `_score_item` now handles list-type fields (backward compatible)
- `_TERM_ALIASES` expanded with playbook/publish vocabulary

## [3.22.2] - 2026-05-23

Search discovery and conversion optimization release.

### Changed
- README rewritten with pain-point language for GEO/SEO/AIEO search discovery
- Per-client config blocks added (Claude Code, Cursor, Codex, Claude Desktop, Windsurf)
- FAQ rewritten with search-optimized Q&A for AI citation
- Chinese README synced with English version
- pyproject.toml description and keywords updated for search discovery
- MCP Registry description updated to "persistent memory" framing

## [3.22.1] - 2026-05-23

MCP Registry distribution release.

### Added
- Official MCP Registry `server.json` (`.mcp/server.json`)
- `mcp-name` tag in README for PyPI ownership verification
- CODE_OF_CONDUCT.md (Contributor Covenant v2.0)

### Changed
- Smithery listing published and set to public

## [3.22.0] - 2026-05-23

Doctor upgrade and onboarding polish release.

### Added
- **`engram doctor` functional checks**: After config health scan, doctor now verifies core library import, Engram initialization, identity profile, quick_context.md, and MCP tool registration
- **Setup post-completion verification guide**: Clear next-step instructions after setup finishes

### Changed
- CI workflows opt into Node.js 24 (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`), eliminating GitHub deprecation warnings
- Shared instructions cleaned up: removed 30 lines of stale version history, updated Tier-1 tool list to 13 tools

### Fixed
- CHANGELOG v3.21.0 tool count corrected (was 43→46, now 45→48)

## [3.21.0] - 2026-05-23

Agent context auto-save release — recover lost AI conversations.

### Added
- **Agent context auto-save**: Office-style autosave for AI session context. Silently records work state at key checkpoints (task start, milestone, direction change); recoverable on demand after tool restart or session disconnect
- **`save_agent_context` MCP tool**: Save or append context checkpoints per tool, with session ID for multi-checkpoint sessions
- **`get_recent_context` MCP tool**: Retrieve the most recent session context after context loss (tool restart, session disconnect)
- **`list_agent_sessions` MCP tool**: Browse available session records across all tools (metadata only)
- **`ContextStoreMixin`** (contexts.py): New mixin with per-tool session file storage in `~/.engram/contexts/{tool}/`
- Storage: append-only markdown files, never auto-expire or auto-delete
- 14 new tests for context save, append, recovery, listing, and tool isolation
- All 3 context tools added to Tier-1 (always available)

### Changed
- MCP Tier-1 tools increased: 10 → 13 (added save_agent_context, get_recent_context, list_agent_sessions)
- MCP tool count increased: 45 → 48
- Directory structure: `contexts/` added to `~/.engram/` on init

## [3.20.0] - 2026-05-23

Knowledge health scoring and smart deduplication release.

### Added
- **Knowledge health score**: `get_knowledge_overview(section="health")` now returns a 0–100 composite `health_score` with four-dimension breakdown: freshness (% reviewed within 30 days), quality (verified vs staging ratio), coverage (domain diversity via Shannon entropy), cleanliness (absence of duplicates/archive candidates)
- **`suggest_merges` MCP tool**: Full-knowledge-base scan for near-duplicate items above a similarity threshold (default 0.45). Returns actionable merge commands — each suggestion includes primary/secondary IDs, summaries, similarity score, and a ready-to-call `merge_knowledge()` command
- Tests for health scoring dimensions and suggest_merges functionality

### Changed
- README updated to describe health score dimensions and `suggest_merges` tool
- MCP tool count increased: 19 read + 17 write + 1 web + 4 import/export + 2 workflow = 43 tools

## [3.19.0] - 2026-05-23

Cold-start optimization release — solving the "installed but never used" gap.

### Added
- **Environment auto-probing**: `engram setup` now detects name, email (from git config), tech stack (from project files), language preference (from commit history), and commit style automatically
- **Seed knowledge templates**: Setup injects best-practice lessons based on detected tech stack (Python, TypeScript, Go, Rust, Java + universal), marked as `staging` tier
- **Guided empty-state response**: `get_user_context` on empty Engram now returns a 5-step AI onboarding guide instead of a bare "no context" message
- **Auto-refresh `quick_context.md`** at end of setup wizard — all AI tools can read it immediately
- **Distribution monitoring script** (`scripts/metrics.py`): tracks GitHub traffic, PyPI downloads, referral sources, and local usage signals
- 4 new tests for cold-start functions (probe, seed templates, dedup, empty dir)

### Changed
- **Supported tools table expanded to 13 entries** in README (was 6): 4 verified + 7 expected-to-work + OpenClaw + ChatGPT fallback
- **"Status" column renamed to "Confidence"** for clearer messaging
- Setup menu options now pre-ordered based on probed environment signals

## [3.18.0] - 2026-05-23

Repo rename, security hardening, and doctor upgrade release.

### Changed
- **GitHub repo renamed** `Patdolitse/engram` → `Patdolitse/piia-engram` (avoids collision with Gentleman-Programming/engram 3.7k stars)
- **Module rename completed** across all files: `engram_core` → `piia_engram` (backward-compat shim retained with `DeprecationWarning`)
- **`engram doctor` expanded to 11 AI tools** (was 6): Claude Code, Cursor, Claude Desktop, Codex + 7 community-supported (Windsurf, Copilot, Cline, Roo Code, Amazon Q, Augment, Zed)
- **Doctor output now shows verified vs community tiers** — clear labeling of team-tested vs untested tools
- **Social preview images updated** with piia-engram branding

### Security
- **Removed 20 tracked result/data files** from git (benchmark outputs, evaluation logs with LLM payloads)
- **Scrubbed 4 hardcoded personal paths** (Windows username) from reports and docs
- **.gitignore hardened** — added `.env.*`, `*.pem`, `*.key`, `credentials*`, `secrets*`, broader evaluation result patterns
- **CI workflows locked down** — explicit `permissions: contents: read` on both ci.yml and publish.yml

### Tests
- **674 passed**, 0 failed
- Post-rename verification: 10/10 checks PASS (old imports, URLs, package metadata, CLI entry points, doctor coverage, backward compat)

## [3.17.0] - 2026-05-23

Quality & reliability release: 657 tests at 96% coverage (all modules ≥90%), cross-platform CI fixes, and Round 10 retrieval quality benchmark achieving 43/43 PASS.

### Added
- **Cold-start setup streamlining** — simplified first-run experience with guided setup flow
- **Round 10 retrieval/injection quality benchmark** — 7-dimension, 43-case test suite; all 43 PASS with DeepSeek V4 Pro judge

### Fixed
- **CI stability** — safe tilde expansion (no `os.path.expanduser` on `~` in path literals), test auth hardening, job matrix reduced 12→6 for faster feedback
- **Cross-platform path parsing** — `_sanitize_project()` uses `PureWindowsPath` so Windows paths parse correctly on all platforms

### Tests
- **657 passed** (up from 490 in v3.16.0; +167 new)
- Total coverage: **83% → 96%** (+13pp); all modules ≥90%
- Key module coverage: storage 100%, core 95%, reconcile 98%, mcp_server 99%, setup_wizard 93%, reports_identity 100%, stats 100%

### Benchmarks
- Round 10: retrieval quality 43/43 PASS across 7 dimensions (relevance, completeness, noise, format, latency, edge cases, injection safety)

## [3.16.0] - 2026-05-22

Code quality release: split the last monolithic module, brought mcp_server coverage to production-grade, and ran third-party milestone evaluation.

### Changed
- **`reports.py` split into 5 modules** (1103 lines → max 520 per file):
  - `reports.py` (22 lines) — thin hub composing 4 sub-mixins
  - `reports_rarity.py` (85 lines) — `RarityMixin`: quality classification + `RARITY_TIERS`
  - `reports_review.py` (520 lines) — `ReviewMixin`: HTML review page, promote/archive
  - `reports_identity.py` (97 lines) — `IdentityCardMixin`: Markdown identity card export
  - `reports_analytics.py` (310 lines) — `AnalyticsMixin`: health reports, stale detection, digest, stats
- Public API unchanged — `from piia_engram.reports import ReportsMixin` still works
- `architecture.md` updated to v3.16.0 with new module map and two-level mixin diagram
- README "By the numbers" updated to v3.16.0 stats (490 tests, 83% coverage)
- CONTRIBUTING test baselines updated: 490+ tests, 83%+ coverage

### Tests
- **490 passed** (up from 437 in v3.15.1; +53 new)
- New `tests/test_mcp_coverage.py` (53 tests) — covers write tools, search, review/merge, identity update, import/export, workflow shortcuts, and all 7 MCP resources
- `mcp_server.py` coverage: **58% → 86%** (+28pp)
- Total coverage: **78% → 83%** (+5pp)

### Evaluated
- DeepSeek 3-pass milestone evaluation: architecture 8.0 (+0.5), security 8.0 (+0.5), overall 7.53
- 5/5 v3.14.3 suggestions verified as fixed
- Key feedback: architecture.md and CONTRIBUTING.md were lagging (now fixed)

## [3.15.1] - 2026-05-22

### Fixed
- **GBK console safety**: Identity card preview in setup wizard now uses `_safe_print()` to avoid `UnicodeEncodeError` on Windows Chinese consoles (strips unsupported emoji, preserves CJK text)

### Improved
- **README**: Added PyPI download badge, "30 seconds" quick start framing, setup step 5-6 (privacy + identity card preview), updated "By the numbers" to v3.15.0 stats (437 tests), added CLI commands reference section
- **README.zh-CN.md**: Synced all English README improvements
- **CONTRIBUTING baselines**: 394+ → 437+ tests

## [3.15.0] - 2026-05-22

Privacy-focused feature release: opt-in anonymous usage statistics, reconcile authorization gate, and setup wizard privacy step. Designed through cross-AI consultation (4 independent AI evaluations synthesized).

### Added
- **Anonymous usage statistics (Phase 1: local log only)** — `telemetry.py` module
  - Off by default; opt-in during `engram setup` Step 5 or via `engram telemetry on`
  - Collects only 4 fields: tool call distribution (success/error counts), knowledge entry totals, engram version, daily anonymous ID
  - Daily ID via `HMAC(local_uuid, date)` — cannot link across days
  - Payload validator rejects strings >200 chars or with natural language patterns (no content leakage possible)
  - All data stored locally in `~/.engram/telemetry.log` (JSONL, human-readable)
  - **No network requests** — Phase 2 gated by 30 days + 5 users sharing logs
  - CLI: `engram telemetry status|preview|on|off`
  - Env override: `ENGRAM_TELEMETRY=0|1`
- **Reconcile authorization gate** — `reconcile.py`
  - `reconcile_memories()` and `reconcile_ai_configs()` now require explicit authorization
  - Controlled via `ENGRAM_RECONCILE` env var or `telemetry_config.json` preference
  - Default: authorized (backward-compatible for existing users)
  - New users explicitly choose during setup
- **Setup wizard Step 5: Privacy Preferences**
  - [1] Cross-tool memory sync authorization (default: Yes)
  - [2] Anonymous usage statistics (default: **No**)
  - Numeric selection UI (no free-text input)
- **ToolCallTracker wired into MCP server** — 10 Tier-1 tools instrumented with success/error tracking; auto-flush during `wrap_up_session`
- **`docs/telemetry_roadmap.md`** — Phase 1 spec, Phase 2 decision gate criteria, cross-AI consultation record

### Changed
- `README.md` / `README.zh-CN.md`: updated "0 network calls" claim to reflect opt-in statistics; FAQ rewritten
- `SECURITY.md`: updated from "no telemetry" to describe opt-in anonymous statistics with preview/off instructions
- `docs/comparison.md`: corrected "no opt-out telemetry" claim to describe opt-in model

### Tests
- **424 passed** (up from 394 in v3.14.4; +30 new)
- New `tests/test_telemetry.py` (30 tests): config persistence, env overrides, daily ID properties, payload validation (length/language/nested), build_payload gating, local log append, preview, ToolCallTracker lifecycle, opt-out safety
- CONTRIBUTING baseline raised: 394+ → **424+ tests**

## [3.14.4] - 2026-05-22

Patch driven by the v3.14.3 DeepSeek milestone evaluation ([report](docs/milestone_review_v3.14.3.md)).
Two HIGH-severity findings addressed; full regression context in the evaluation report.

### Security
- **`crypto.py`: `DecryptionError` + `strict=True` mode**. The default `decrypt()` still returns the original ciphertext on failure (backward-compatible warning + passthrough), but new callers can now opt into `decrypt(value, strict=True)` / `decrypt_fields(..., strict=True)` to raise `DecryptionError` instead. Uses `raise from None` to avoid leaking timing-oracle info about which stage failed (b64 / key derivation / AEAD tag).
- The default behavior preserves backward compatibility for any caller that may already depend on it, but the docstring now explicitly warns: "callers that don't validate the prefix after this call may treat ciphertext as plaintext — prefer strict=True in new code."

### Fixed
- **README MCP tool count inconsistency**. README's "By the numbers" / 量化数据 section claimed 45 tools while elsewhere said 43; actual count is **43** (`grep -c '^@mcp.tool' src/piia_engram/mcp_server.py`). All documents now consistent at 43:
  - `README.md` and `README.zh-CN.md` quantitative sections + comparison tables
  - `docs/comparison.md`
  - `docs/architecture.md` (3 references)
  - `docs/coverage_baseline_v3.14.2.md`
  - `experiments/evaluations/v3.14.3/evidence_pack.md` (with explicit erratum note)

### Tests
- **394 passed** (up from 386 in v3.14.2; v3.14.3 was docs-only)
- New `TestDecryptionStrict` class in `tests/test_crypto.py` (8 tests): wrong-key raises, bad payload raises, truncated payload raises, unprefixed passthrough in strict mode, happy-path round trip, default mode unchanged, `__cause__` is None (no timing leak), `decrypt_fields(strict=True)` raises without mutating input dict
- CONTRIBUTING baseline raised: 386+ → **394+ tests**

### Docs
- New `docs/milestone_review_v3.14.3.md` — full v3.13.2 → v3.14.3 evaluation closure (4-pass DeepSeek)
  - Architecture score: 5.4 → 7.50 (+2.10, biggest movement)
  - Overall: 6.9 → 7.90 (+1.00)
  - Self-assessment calibration bias narrowed from +1.7 (security blind spot) to −0.5 (now slightly conservative)
  - 15/21 v3.13.2 issues marked `fixed`, 5 `partial`, 1 `unverified`, 0 `regression`
  - Roadmap items extracted for v3.15.0: split reports.py (1103 lines), explicit Mixin dependencies, add SSE integration tests, mock LLM extraction

## [3.14.3] - 2026-05-22

### Docs
- New `docs/architecture.md` — 30-second mental model diagram, complete module map (post v3.14.1 refactor), three canonical data flows (cold start / capture / review), storage layout, MCP surface, conventions, "where to add things" matrix
- New `docs/comparison.md` — factual side-by-side with Letta, Mem0, Cline memories, Claude Code memory; explicit "choose someone else when..." section; identity-layer vs memory-layer architectural framing
- README upgrade: comparison table expanded to 5 competitors with clearer dimensions (purpose, locality, encryption, knowledge tiers, conflict detection); new "By the numbers" section with v3.14.2 quantitative claims (45 MCP tools, 386 tests, 78% coverage, PBKDF2 600k, < 100ms cold start, 0 network calls in core); both English and Chinese
- README FAQ: explanation of the `piia-engram` PyPI name vs the "Engram" product brand (English + Chinese)

### Tests
- Unchanged — 386 passed (no code changes in this release)

## [3.14.2] - 2026-05-22

### Tests
- **386 passed** (up from 329 in v3.14.1, +57 new)
- New `tests/test_mcp_tools.py` (37 tests) — direct coverage of MCP tool wrappers: identity reads, knowledge read/write, search, context, error catching, Tier-1 filtering, path validation
- New `tests/test_review_page_xss.py` (10 tests) — verifies `_esc` escaping prevents HTML / attribute injection in the review HTML page (lesson summary, decision title, domain label, profile fields, source_tool, ampersand, CJK passthrough)
- Expanded `tests/test_crypto.py` (+10 tests, now 19) — v1↔v2 mixed-field decryption, v1→v2 re-encryption upgrade, Unicode (emoji/CJK/RTL/combining chars), bad base64 / truncated payload / unknown prefix passthrough, non-string field skip, iteration-count pinning, default-prefix-is-v2 contract

### Security
- **Path validation**: new `_validate_path` helper in `mcp_server.py` rejects NUL bytes in user-supplied paths. Applied to `import_engram`, `export_engram`, `save_project_snapshot`. Engram remains local-first (not a sandbox), but null-byte handling now matches OWASP guidance for paths crossing trust boundaries.

### Docs
- New `docs/coverage_baseline_v3.14.2.md` — first published coverage baseline: **78% total**, 8 modules ≥85%, gaps documented for `mcp_server.py` (54%, SSE + uncalled wrappers) and `setup_wizard.py` (58%, interactive flow)
- New `.coveragerc` — pins source root and exclude rules so future runs are reproducible
- CONTRIBUTING baseline raised: 329+ tests → 386+ tests, 78%+ coverage required

## [3.14.1] - 2026-05-22

### Refactor
- **`core.py` split**: 4277 → 1083 lines (-74.7%), extracted into 7 modules via mixin pattern. Public API unchanged — all imports from `piia_engram.core` continue to work via re-exports.
  - `storage.py` (224) — constants + I/O primitives (`_read_json`, `_write_json`, `_engram_root`, etc.)
  - `retrieval.py` (639) — `RetrievalMixin`: search, scoring, tokenization, batch ops, conflict detection
  - `context.py` (688) — `ContextMixin`: `generate_context`, ingestion + standalone `extract_knowledge` / `ingest_extraction`
  - `reconcile.py` (425) — `ReconcileMixin`: external AI memory + config file sync
  - `reports.py` (1103) — `ReportsMixin`: review HTML, identity card, health, stats, knowledge digest
  - `compat.py` (318) — OpenClaw / OCA migration functions
  - `core.py` (1083) — `Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)` facade

### Security
- **PBKDF2 iterations: 100,000 → 600,000** (OWASP 2023+ recommended floor). New encryptions use `enc:v2:` prefix.
- **Backward compatibility**: `enc:v1:` ciphertexts (legacy 100k iterations) continue to decrypt. Old data is re-encrypted to v2 on next write of that field.

### Fixed
- **Schema version comparison**: `_migrate_v1_to_v2` used lexicographic string comparison (`"10.0" < "2.0"`). Now parses to tuples via `_parse_schema_version`.

### Changed
- **`print(file=sys.stderr)` → `logging`** across all piia_engram modules (audit, compat, context, crypto, mcp_server, setup_wizard, stats, storage). Each module gets `logger = logging.getLogger(__name__)`. Library output is now respectful of host application's logging config.

### Tests
- **329 passed** (up from 328 in v3.14.0)
- New: `test_v1_ciphertext_still_decrypts` — verifies forward decryption of legacy v1 ciphertexts after the PBKDF2 upgrade

## [3.14.0] - 2026-05-22

### Breaking
- **Encryption fail-fast**: `EncryptionEngine` now raises `RuntimeError` when `ENGRAM_SECRET` is set but `cryptography` package is missing. Previously it silently disabled encryption, risking plaintext storage.

### Security
- **Timing attack fix**: SSE token comparison changed from `==` to `secrets.compare_digest`
- **SECURITY.md corrected**: "Fernet" → "AES-256-GCM" to match actual implementation
- **SSE hardening**: `0.0.0.0` bind emits HTTPS warning; new `ENGRAM_CORS_ORIGINS` env var for cross-origin restriction
- **sys import fix**: `core.py` was missing top-level `import sys` — error handlers would have raised `NameError` instead of logging

### Fixed
- `_apply_tool_tier` docstring corrected (core is the default, not all)
- Removed redundant `import sys as _sys` in mcp_server.py startup sync block
- README: "100% local" → "local-first" (honest about `read_web_content` network path)
- README: "automatically" → "one tool call away" (knowledge inheritance requires explicit call)
- README: stale knowledge days 90 → 30 (matches `STALE_KNOWLEDGE_DAYS` constant)
- README FAQ: installation path unified to `pip install piia-engram && engram setup`
- README: added `ENGRAM_TOOLS=all` config example with JSON snippet
- README: added `ENGRAM_CORS_ORIGINS` to SSE security notes
- All fixes applied to both English and Chinese README

### Tests
- 328 passed (up from 327 in v3.13.2)
- New: `test_secret_without_crypto_raises` — verifies fail-fast on missing cryptography

### Docs
- v3.13.2 milestone evaluation report (`docs/milestone_review_v3.13.2.md` + `.html`)

## [3.13.2] - 2026-05-22

### Tests
- **327 passed** (up from 281 in v3.13.1) — 46 new tests covering critical algorithm gaps
- New: 7 `_score_item` tests (field weights, access bonus, multi-term coverage, CJK queries)
- New: 4 `search_knowledge` tests (ranking, CJK search, alias expansion, threshold filtering)
- New: 4 `_detect_decision_conflicts` tests (same/different domains, overlapping domains)
- New: 4 `_detect_lesson_conflicts` tests (negation/affirmation markers, CJK, domain separation)
- New: 7 `generate_context` tests (empty profile, token budget, conflict section, section inclusion)
- New: 8 `ingest_notes` tests (decision/lesson triggers, short line skipping, dedup, CJK triggers)
- New: 4 `_infer_domain` tests (single/multi match, fallback behavior)
- New: 4 `_bigram_similarity` tests (identical, empty, partial, completely different)
- New: 2 `evaluate_tiers` + 1 eviction test (staging-first eviction policy)

## [3.13.1] - 2026-05-22

### Fixed
- **CJK line classification**: Chinese lines (e.g. "我是全栈开发者") were incorrectly skipped during rule file import because the minimum length threshold (8 chars) didn't account for CJK character density. Now uses 4-char threshold for CJK text.
- **Rule directory globbing**: `reconcile_ai_configs` now correctly imports rule files from directory-style configs (e.g. `~/.cursor/rules/*.mdc`) instead of silently skipping them.
- **Stale knowledge display**: 3 remaining hardcoded "30 天" strings now use the `STALE_KNOWLEDGE_DAYS` constant consistently.

### Tests
- 281 passed (up from 258 in v3.13.0)
- New: 22 parametrized `_classify_line` tests covering CJK, user identity, project rules, skip, and ambiguous cases
- New: 2 `_scan_rule_files` tests (project detection + tiny file skip)
- New: `reconcile_ai_configs` directory globbing test

## [3.13.0] - 2026-05-22

### Breaking
- **Default tool set changed to Tier-1 Core (10 tools)**. Previously all 43 tools were loaded by default. Set `ENGRAM_TOOLS=all` in your MCP config `env` to restore the full set. `engram doctor` will show an info notice if your config doesn't specify `ENGRAM_TOOLS`.

### Changed
- **Tier-1 tool set revised**: added `wrap_up_session` (session lifecycle) and `update_identity` (profile updates); removed `extract_session_insights` and `export_engram` (moved to Tier-2)
- **Quickstart simplified**: `pip install piia-engram && engram setup` is the complete flow; manual MCP JSON config moved to collapsible section
- README tool tables reorganized: Tier-1 as main table, Tier-2 in collapsible `<details>` section

### Improved
- `engram doctor` shows info notice when configs lack `ENGRAM_TOOLS` setting
- Extracted `MAX_KNOWLEDGE_ENTRIES` constant (was hardcoded `200` in 11 places)

## [3.12.3] - 2026-05-22

### Fixed
- **JSON corruption logging**: `_read_json()` now warns to stderr on parse failure instead of silently returning empty data
- Last 3 silent exception blocks (stats.py, crypto.py) now log to stderr — **zero silent exceptions** across all source files

### Improved
- Extracted `SEARCH_RELEVANCE_THRESHOLD`, `STALE_KNOWLEDGE_DAYS`, and `MAX_KNOWLEDGE_ENTRIES` as module constants (was hardcoded in 17 places total)
- CI workflow: added pip caching for faster runs
- README tool tables now list all 43 tools (was missing `apply_review` and `request_outline_review`)
- Replaced `__import__('sys')` hacks with proper imports

### Tests
- 258 passed (up from 242 in v3.12.2)
- New: 12 tests for staging/review/rarity workflow (classify_rarity, evaluate_tiers, apply_review, promote_knowledge)
- New: 3 tests for export_all/import_all error handling

## [3.12.2] - 2026-05-22

### Added
- **Search alias expansion**: 16 new CJK/English alias pairs (js→javascript, db→数据库, 部署→deploy, 前端→frontend, etc.)
- **CJK trigram alias lookup**: 3-character Chinese terms (e.g. "数据库") now correctly expand to English aliases during search

### Improved
- Removed redundant `test.yml` workflow — `ci.yml` already covers 3 OS × 4 Python versions

### Tests
- 242 passed (up from 224 in v3.12.1)
- New: 16 tests for `export_to_openclaw`, `import_from_openclaw`, `migrate_from_oca_memory`, `increment_domain_usage`
- New: 2 alias expansion tests (abbreviation + cross-language search)

## [3.12.1] - 2026-05-22

### Fixed
- **Search ranking**: multi-term queries now correctly prioritize items matching more query terms via coverage bonus (D6-RANK-01 benchmark fix)

### Improved
- SPDX license format in pyproject.toml (silences setuptools deprecation warnings)
- pytest `pythonpath` config replaces `sys.path.insert` hack in all test files

### Tests
- Round 10 benchmark: 43/43 (100%), up from 40/43

## [3.12.0] - 2026-05-22

### Improved
- Cold-start empty-state guidance: actionable next steps (update_identity / engram setup) instead of bare warning
- All silent `except Exception: pass` blocks now log to stderr for debugging
- Python 3.13 added to CI test matrix and PyPI classifiers

### Tests
- 212 passed (up from 193 in v3.11.2)
- New: `test_stats.py` — 11 tests covering stats module (API mocking)
- New: 3 `engram doctor` tests (healthy config, legacy name, invalid path)
- New: 5 edge-case tests (token budget, CJK conflict, config size limit)

### Docs
- Bilingual issue templates (bug report + feature request)
- Bilingual PR template with security checklist
- Consolidated 9 individual RELEASE_NOTES files into CHANGELOG.md
- Bilingual docstrings for review tools

## [3.11.2] - 2026-05-22

### Security
- `export_identity_card()` now respects `trust_boundaries.restricted_fields`
- `get_profile` MCP tool default changed to `safe=True`
- `engram://identity/profile` resource endpoint now returns safe (filtered) profile
- Field whitelist validation on `update_profile`, `update_preferences`, `update_trust_boundaries`, `update_quality_standards`
- `reconcile_memories` skips files > 10 KB; `reconcile_ai_configs` skips files > 50 KB
- Audit log written after every reconcile run

### Tests
- 193 passed (7 new security tests)

## [3.11.1] - 2026-05-22

### Changed
- Version bump for PyPI (3.11.0 filename already occupied)

## [3.11.0] - 2026-05-22

### Added
- **Knowledge conflict detection** — `generate_context()` warns about contradictory decisions (same domain + similar question + different choice) and contradictory lessons (sentiment asymmetry)
- **Token budget control** — `generate_context(max_tokens=N)` drops low-priority sections first; 11 sections ranked by priority
- **Staging backlog reminder** — `wrap_up_session` and `generate_context()` notify about unreviewed auto-imported items
- **Simplified rarity system** — 3+1 tiers (legendary/epic/rare + staging gray); staging-first eviction on truncation
- **Auto-sync** — `reconcile_memories()` + `reconcile_ai_configs()` import from Claude Code memory, CLAUDE.md, .cursorrules, etc.
- **Interactive review page** — browser-based knowledge review with domain grouping, rarity badges, retain/archive toggles
- SECURITY.md — bilingual vulnerability reporting policy
- NOTICE — Apache 2.0 attribution file

### Fixed
- **P0**: Truncation now evicts staging items first (never drops verified knowledge)
- **P1**: Staging auto-promote removed; promotion only via `evaluate_tiers()`
- **P1**: XSS — all user-controlled HTML fields escaped via `_esc()`, including domain group titles
- **P1**: Archive false success — `apply_review` checks `result.get("error")` properly
- **P1**: Frontmatter parsing — `---` in content body no longer toggles frontmatter mode
- **P1**: Nested project paths — greedy recursive `_decode_claude_project_name()`
- **P2**: Review page `access_count` pollution — `generate_review_page()` uses `_update_access=False`

### Tests
- 186 passed; Round 10 benchmark 43/43 across 7 dimensions

## [3.10.1] - 2026-05-19

### Fixed
- Context quality hotfix: lesson allocation, domain sanitization, empty profile guidance

## [3.10.0] - 2026-05-18

### Added
- Bilingual MCP tool descriptions (Chinese + English)
- Bilingual setup wizard with numbered menu selection
- SSE transport mode for remote deployment
- Token-based authentication middleware

### Tests
- Round 9 lifecycle verification: T1 10/10, T2 20/20

## [3.9.0] - 2026-05-15

### Added
- 10-minute aha onboarding with smart scan + split import
- Auto-detect existing AI tool configs during setup

## [3.8.1] - 2026-05-13

### Fixed
- AI context injection no longer pollutes staleness detection

## [3.8.0] - 2026-05-12

### Added
- Knowledge lifecycle tools: `review_knowledge`, `get_stale_knowledge`
- Domain parameter on `get_decisions` and `add_decision`
- Multi-label domain support (comma-separated)

### Tests
- Round 7: domain softening T1 15/15, T2 19/20
- Round 8: decisions domain T1 8/8, T2 20/20

## [3.7.0] - 2026-05-09

### Added
- Optimized tool descriptions and workflow shortcuts (39 tools)
- Round 6 full coverage benchmark: 39 tools, 88 scenarios, 98.9% accuracy

## [3.6.0] - 2026-05-07

### Fixed
- Include decisions in cold-start context and identity card

## [3.5.1] - 2026-05-05

### Added
- Onboarding seed knowledge, MCP tool tiering, narrow ICP

## [3.5.0] - 2026-05-03

### Added
- Sharpened positioning as AI identity layer

## [3.4.0] - 2026-04-30

### Added
- Personal knowledge card (PKC) export and identity card improvements

## [3.3.0] - 2026-04-27

### Added
- Audit logging for all read/write operations

## [3.2.0] - 2026-04-24

### Added
- Encryption at rest (AES-256-GCM) for sensitive profile fields

## [3.1.0] - 2026-04-21

### Added
- Trust boundaries and restricted fields

## [3.0.0] - 2026-04-18

### Changed
- Major architecture rewrite: MCP-native, modular core engine
- Knowledge stored as structured JSON (lessons + decisions)

## [2.9.0] - 2026-04-15

### Added
- Weighted multi-term search + `find_similar_knowledge`

## [2.6.0] - 2026-04-12

### Added
- Weighted search scoring

## [2.5.0] - 2026-04-09

### Added
- Bulk knowledge import + note ingestion

## [2.4.0] - 2026-04-06

### Added
- Bidirectional knowledge linking

## [2.3.0] - 2026-04-03

### Added
- Knowledge quality: aging, digest, report export

## [2.2.0] - 2026-03-31

### Added
- Atomic writes, file locking, restricted_fields enforcement

## [2.1.0] - 2026-03-28

### Added
- Knowledge search, lifecycle management, health report

## [2.0.0] - 2026-03-25

### Added
- Initial release: AI identity layer with profile, work style, lessons, decisions
- MCP server with stdio transport
- Apache 2.0 license
