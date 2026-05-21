# Engram v3.11.0 — Conflict Detection, Token Budget, Quality Baseline

**Release date**: 2026-05-22

## New Features

### 1. Knowledge Conflict Detection
`generate_context()` now detects and warns about contradictory knowledge before injecting it into AI system prompts.

- **Decision conflicts**: Same domain + similar question + different choice (bigram similarity threshold 0.25)
- **Lesson conflicts**: Same domain + shared topic keyword + sentiment asymmetry (affirm vs negate)
- Warning appears as `知识冲突提醒` section in context, asking AI to confirm with user
- Zero false positives on unrelated topics or same-choice decisions

### 2. Token Budget Control
`generate_context(max_tokens=N)` dynamically allocates sections by priority when system prompt space is limited.

- 11 sections ranked by priority: profile > lessons > decisions > preferences > quality > conflicts > project > domains > stale > staging > sync
- Lower-priority sections dropped first when budget is tight
- Token estimation for mixed CJK/ASCII without tiktoken dependency
- `max_tokens=None` (default) preserves backward-compatible behavior

### 3. Staging Backlog Reminder
Users are now notified about unreviewed auto-imported knowledge.

- `get_staging_summary()` counts active staging lessons and decisions
- `wrap_up_session` includes `staging_reminder` in results when backlog exists
- `generate_context()` adds `staging_review_reminder` when staging > 10 items
- Review page adds "Show staging only" filter button

### 4. Simplified Rarity System
Reduced from 6 color tiers to 3+1 for clarity.

- Verified items: legendary (gold) / epic (purple) / rare (blue)
- Staging items: always gray, no stars
- Staging-first eviction on truncation (protects verified knowledge)
- `evaluate_tiers()` batch promotion only via access_count >= 3

### 5. Auto-Sync (reconcile_memories + reconcile_ai_configs)
Automatic discovery and import of external AI tool memories and configurations.

- Scans Claude Code auto-memory, CLAUDE.md, .cursorrules, copilot-instructions.md, etc.
- Bigram dedup (threshold 0.55) prevents duplicate imports
- Runs silently during cold-start and wrap_up_session

### 6. Interactive Knowledge Review Page
Browser-based review interface for auditing all stored knowledge.

- Dark theme HTML with domain grouping, rarity badges, stale indicators
- Per-item retain/archive toggles with bulk select
- Staging filter button with XSS-safe domain rendering
- Bilingual (zh/en) support

## Bug Fixes

- **P0: Silent data loss** — Truncation now evicts staging items first, never drops verified knowledge
- **P1: Staging auto-promote** — Removed read-time tier evaluation; promotion only via explicit `evaluate_tiers()`
- **P1: XSS** — All user-controlled HTML fields escaped via `_esc()`, including domain group titles
- **P1: Archive false success** — `apply_review` now checks `result.get("error")` properly
- **P1: Frontmatter parsing** — `---` in content body no longer toggles frontmatter mode
- **P1: Nested project paths** — Greedy recursive `_decode_claude_project_name()` replaces shallow matching
- **P2: Review page access_count pollution** — `generate_review_page()` uses `_update_access=False`

## Quality Assurance

### Round 10 Retrieval/Injection Quality Benchmark
43 test cases across 7 dimensions — **all PASS**:

| Dimension | Cases | Result |
|-----------|-------|--------|
| D1 Context Assembly | 8/8 | PASS |
| D2 Token Budget | 6/6 | PASS |
| D3 Recall Precision | 8/8 | PASS |
| D3 Recall Quality (LLM) | 1/1 | PASS |
| D4 Identity Fidelity (LLM) | 5/5 | PASS |
| D5 Stale/Conflict Detection | 7/7 | PASS |
| D6 Search Scoring | 7/8 | PASS |

### Unit Tests
- **186 passed** (up from 156 in v3.10)
- New test areas: conflict detection (5), staging reminder (5), token budget (4), tier system (5), bug regressions (6)

## Open Source

- **LICENSE**: Apache 2.0 (unchanged)
- **NOTICE**: Added — project attribution and third-party dependencies
- **SECURITY.md**: Added — vulnerability reporting policy and security design principles

## Upgrade Notes

No breaking changes. `generate_context()` accepts an optional `max_tokens` parameter; omitting it preserves previous behavior. Conflict detection and staging reminders activate automatically.
