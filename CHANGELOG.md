# Changelog

All notable changes to Engram are documented in this file. For detailed release notes with upgrade instructions, see [GitHub Releases](https://github.com/Patdolitse/engram/releases).

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

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
