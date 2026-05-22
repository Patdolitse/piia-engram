# Engram v3.16.0 Independent Code Evaluation - Codex

Evaluator: `codex-gpt-5`  
Timestamp: `2026-05-22T17:28:59+08:00`  
Scope: read full local codebase; no changes to `src/` or `tests/`.

## 1. Fact Table

| Item | Result | Evidence |
|---|---:|---|
| Full test suite | 490 passed | `python -m pytest tests/ -q` -> `490 passed in 36.33s` |
| Verbose test run | 490 passed | `python -m pytest tests/ -v --tb=short` -> `490 passed in 34.20s` |
| Total coverage | 83% | `python -m pytest tests/ --cov=engram_core --cov-report=term-missing -q` |
| `mcp_server.py` coverage | 86% | coverage table |
| Source `.py` files | 18 | `src/engram_core/*.py` |
| Largest source file | `src/engram_core/mcp_server.py`, 1411 lines | line count |
| `core.py` lines | 1097 | line count |
| `reports.py` hub lines | 20 | line count |
| MCP tools | 43 | `rg '^@mcp\\.tool' src/engram_core/mcp_server.py` |
| Compile check | passed | `python -m compileall -q src` |

Changelog versions after v3.14.3: `3.16.0`, `3.15.1`, `3.15.0`, `3.14.4`.

README quantitative claims are mostly correct: 43 tools, 490 tests, 83% total coverage, and 86% `mcp_server.py` coverage all match the local run. One minor stale number remains: README says `core.py` is 1088 lines, while the local file count is 1097.

## 2. Scores

| Dimension | Score | Rationale |
|---|---:|---|
| Architecture | 8 | `core.py:64` composes `RetrievalMixin`, `ContextMixin`, `ReconcileMixin`, `ReportsMixin` cleanly. `reports.py:13-20` is now a thin compatibility hub. The remaining drag is `mcp_server.py` at 1411 lines and mixed wrapper/auth/resource/telemetry responsibilities. |
| Testing | 8 | 490 tests pass, total coverage is 83%, `mcp_server.py` is 86%. `tests/test_mcp_coverage.py` includes real persistence/error-path assertions, and `tests/test_mcp_tools.py:300-361` covers path validation. Gaps remain around telemetry integration coverage. |
| Security | 7 | Good defaults: usage stats disabled unless enabled (`telemetry.py:174`), setup default is No (`setup_wizard.py:685-689`), review HTML escapes user data (`reports_review.py:28, 80-100`), and NUL paths are rejected (`mcp_server.py:141-149`). The payload validator does not inspect dict keys. |
| Documentation | 7 | README and SECURITY are largely aligned with v3.16.0. But `CONTRIBUTING.md:28` still says no telemetry, `docs/comparison.md:34` still says 386 tests / 78%, and `docs/telemetry_roadmap.md:55` calls Phase 2 v3.16.0 although current code is Phase 1 local-only. |
| Positioning | 8 | The "AI identity layer" positioning remains strong: local JSON, cross-tool MCP, user-owned context, not an agent memory DB. Residual documentation drift weakens the trust story more than the product story. |

Overall: **7.6 / 10**

## 3. DeepSeek Comparison

DeepSeek gave: architecture 8.0, testing 7.0, security 8.0, documentation 6.67, positioning 8.0, overall 7.53.

I broadly agree with the overall band, but redistribute the score:

- Testing should be **8**, not 7. I ran the full suite and inspected the tests. Many are not "call-only"; they assert persisted fields, JSON shapes, path errors, duplicate status, and workflow output.
- Security should be **7**, not 8. The design is privacy-conscious, but `_validate_payload` checks string values only, not dict keys. Tool names are dict keys, so a future bad caller could smuggle content or paths through a key.
- Documentation should be **7**, not 6.67. README/SECURITY have been repaired for opt-in usage statistics, but old claims remain in CONTRIBUTING, comparison, and telemetry roadmap.

## 4. Top 3 Issues

### 1. Telemetry payload validator ignores dictionary keys

Severity: **medium**

`src/engram_core/telemetry.py:139-155` recursively validates values, but not keys. Current `mcp_server.py` mostly passes fixed literal tool names, so normal use is not leaking content today. But `ToolCallTracker.record(tool_name, ...)` accepts arbitrary strings, and tool names are serialized as dict keys. A future integration mistake could log natural language or file paths despite the "no content / no paths" guarantee.

This is especially important because `tests/test_telemetry.py:159-170` claims to test content leakage but only checks that hard-coded strings are absent; it does not attempt malicious or accidental content in a dict key.

Recommended fix: validate keys as strings with the same rules, and preferably enforce a static allowlist of known MCP tool names before any Phase 2 network work.

### 2. Usage statistics do not answer the full "which tools are used" question

Severity: **medium**

`mcp_server.py` exposes 43 tools, but `_track(...)` appears only for the Tier-1-like subset: `get_user_context`, `get_identity_card`, `get_project_context`, `get_relevant_knowledge`, `search_knowledge`, `add_lesson`, `add_decision`, `update_identity`, `save_project_snapshot`, and `wrap_up_session`.

That means usage stats cannot reveal whether many non-default or power-user tools are dead code. Examples without `_track`: `ingest_notes` / `extract_session_insights` (`mcp_server.py:692-706`), `update_knowledge` / `archive_knowledge` (`mcp_server.py:710-735`), `review_knowledge`, import/export tools, `start_project` (`mcp_server.py:1240-1280`), and MCP resources.

There is also an ordering bug: `wrap_up_session` flushes stats at `mcp_server.py:1216-1231`, then records `_track("wrap_up_session")` at `mcp_server.py:1235`, so the session-ending call is not included in the flush that most naturally represents that session.

Recommended fix: centralize tracking via a decorator/helper around every `@mcp.tool` wrapper, or explicitly document "only Tier-1 tools are tracked." Record `wrap_up_session` before flushing.

### 3. Documentation still contains stale trust and baseline claims

Severity: **low**

The user-facing README and SECURITY file are much better now:

- `README.md:390-391` explains opt-in anonymous usage statistics.
- `SECURITY.md:33` says usage stats are off by default and excludes content/prompts/paths/IP.

But stale claims remain:

- `CONTRIBUTING.md:28` still says "**100% local** - no cloud, no telemetry, no external calls".
- `docs/comparison.md:34` still says "386, 78% coverage (v3.14.2)" despite local v3.16.0 being 490 / 83%.
- `docs/telemetry_roadmap.md:55` labels Phase 2 as v3.16.0, while current code and setup text are Phase 1 local log only.
- `docs/architecture.md:77-80` lists module line counts that are now materially low for `mcp_server.py`, `telemetry.py`, and `setup_wizard.py`.

Recommended fix: run a final trust-copy sweep before release, especially for any file linked from README.

## 5. Top 3 Strengths

### 1. The release quality claims are real

The headline numbers stand up to local verification: 490 tests, 83% total coverage, 86% `mcp_server.py`, and 43 MCP tools. That is a strong foundation for a small open-source tool.

### 2. The reports split is genuinely cleaner

`reports.py` is now only a composition hub (`reports.py:13-20`), and the responsibilities are understandable:

- rarity scoring in `reports_rarity.py`
- review HTML and promotion/archive in `reports_review.py`
- identity card export in `reports_identity.py`
- health/digest/stats in `reports_analytics.py`

This is a real maintainability improvement over a 1100-line report module.

### 3. The telemetry UX is more respectful than typical analytics

The default is off (`telemetry.py:80`, `setup_wizard.py:685-689`), Phase 1 is local-only (`telemetry.py:1-5`), users can preview payloads (`telemetry.py:218-255`), and `engram telemetry off` is implemented (`setup_wizard.py:985-988`). The daily HMAC ID design (`telemetry.py:113-122`) is also better than a stable uploaded UUID.

## 6. Next Version Priorities

1. **Harden telemetry before network transmission.** Validate dict keys, enforce tool-name allowlist, track all tools or explicitly scope the metric, and add integration tests proving every tracked wrapper records what it claims.

2. **Finish documentation consistency.** Trust is the product. Remove remaining "no telemetry" claims, refresh old coverage/test numbers, and fix Phase 2 version language.

3. **Start decomposing `mcp_server.py`.** It is now the largest file by far at 1411 lines. A next step could split tool groups, resources, transport/auth, and telemetry integration while keeping the public MCP surface stable.

## 7. Release Judgment

This is a good code-quality release, not a perfect one. I would call v3.16.0 **publishable after documentation cleanup**, because the actual runtime behavior is conservative and the tests pass. I would **not** start Phase 2 network telemetry from this state. The payload key validator, incomplete 43-tool tracking, and stale trust docs should be fixed first.
