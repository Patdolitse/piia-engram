# Phase 6 — PR Candidate Backlog

This document lists all identified work items that could become pull requests, organized by category and priority.

---

## Priority 1: Documentation Corrections (Low-Risk, High-Value)

These are factual corrections that don't affect runtime behavior. Good first PRs.

### PR-C-01: Fix stale test count in README.md and docs/comparison.md

**Problem:** README.md:348 says "490 tests", docs/comparison.md:34 says "386 tests / 78%". Actual count is **657 tests** at **83% coverage** (v3.16.0).

**Files to change:**
- `README.md` line 348: "490 tests" → "657 tests"
- `docs/comparison.md` line 34: "386 tests, 78% coverage (v3.14.2)" → "657 tests, 83% coverage (v3.16.0)"

**Risk:** None — purely numeric text corrections.

**Complexity:** ⭐ (very low)

**Verification:** `uv run pytest --co -q` confirms 657 collected.

---

### PR-C-02: Update CONTRIBUTING.md telemetry claim

**Problem:** `CONTRIBUTING.md:28` says "Engram does not send telemetry" — incorrect since v3.15.0 introduced opt-in telemetry (Phase 1 local log).

**Files to change:**
- `CONTRIBUTING.md` — update the telemetry section to reflect opt-in model and point to `docs/telemetry_roadmap.md`

**Risk:** None — documentation only.

**Complexity:** ⭐ (very low)

---

### PR-C-03: Fix `docs/telemetry_roadmap.md` Phase 2 version label

**Problem:** `docs/telemetry_roadmap.md:55` labels Phase 2 as "v3.16.0" despite current code being Phase 1 local-only.

**Fix:** Change "Phase 2 (future version, TBD)" — remove the v3.16.0 version claim.

**Risk:** None — documentation clarification.

**Complexity:** ⭐ (very low)

---

### PR-C-04: Refresh module line counts in docs/architecture.md

**Problem:** `docs/architecture.md:77-80` has stale line counts for `mcp_server.py`, `telemetry.py`, and `setup_wizard.py`.

**Fix:** Re-count lines and update the table.

**Risk:** None.

**Complexity:** ⭐ (very low)

---

### PR-C-05: Fix `core.py` line count in README.md

**Problem:** README.md:349 says `core.py` is 1088 lines; actual is 1097.

**Fix:** "1097" (and verify no other README metrics are stale).

**Risk:** None.

**Complexity:** ⭐ (very low)

---

## Priority 2: Telemetry Improvements (Medium-Risk, Medium-Effort)

### PR-C-06: Validate dictionary keys in telemetry payload

**Problem (from cross-AI report):** `telemetry.py:139-155` recursively validates string values but not dictionary keys. Tool names are dict keys and could theoretically carry accidental content (paths, natural language).

**Fix:**
1. Add key validation to `_validate_payload` — keys must be alphanumeric + underscore, max 64 chars
2. Enforce a static allowlist of known MCP tool names in the tracker
3. Update `test_telemetry.py` with a dict-key test case

**Files:** `src/engram_core/telemetry.py`, `tests/test_telemetry.py`

**Risk:** Medium — changes validation logic; needs thorough test coverage

**Complexity:** ⭐⭐ (medium)

**Severity:** medium — currently not exploitable (tool names are fixed literals), but blocks future Phase 2 network work

---

### PR-C-07: Fix `wrap_up_session` tracking order bug

**Problem (from cross-AI report):** `mcp_server.py:1216-1235` — `wrap_up_session` flushes stats to log, then records `_track("wrap_up_session")`, so the session-ending call is excluded from the flush.

**Fix:** Move `_track("wrap_up_session")` to before the flush call.

**Files:** `src/engram_core/mcp_server.py`

**Risk:** Low — logic fix, no data loss, no security impact

**Complexity:** ⭐⭐ (medium)

---

### PR-C-08: Enforce allowlist for `ENGRAM_TOOLS` env var

**Problem:** `mcp_server.py:79` — `ENGRAM_TOOLS` accepts any string value; only checks `!= "core"` to enable all 43 tools.

**Fix:** Validate `ENGRAM_TOOLS` against an allowlist: `["core", "all"]`. Reject unknown values with a clear error message.

**Files:** `src/engram_core/mcp_server.py`

**Risk:** Low — adds validation, rejects invalid config with a clear message

**Complexity:** ⭐ (low)

---

## Priority 3: Code Quality (Medium-Effort, Long-Term Value)

### PR-C-09: Fix `demos/cross_tool_demo.py` lint violations

**Problem:** `demos/cross_tool_demo.py` has two ruff violations:
- `E402` — module-level import not at top of file (line 25: `from engram_core.core import Engram`)
- `F541` — f-string without any placeholders (line 101: Chinese text with no `{}` placeholders)

**Fix:**
1. Move `from engram_core.core import Engram` to the top of the file (after the `sys.path` hack)
2. Change `print(f"...")` to `print("...")` since the f-string has no placeholders

**Files:** `demos/cross_tool_demo.py`

**Risk:** None

**Complexity:** ⭐ (very low)

---

### PR-C-10: Add type hints enforcement via pre-commit or CI

**Problem:** `mypy` is not installed in the environment and no pre-commit hook enforces type checking. Type annotations exist in the code but are not verified on CI.

**Fix:**
1. Add `mypy` to the `dev` optional dependencies in `pyproject.toml`
2. Run `uv run mypy src/` in CI or as a pre-commit hook
3. Fix any type errors found (pre-existing ones are not blocking, but should be tracked)

**Files:** `pyproject.toml`, possibly `pre-commit-config.yaml` (if it exists)

**Risk:** Low — adding tooling, no code changes required initially

**Complexity:** ⭐⭐ (medium — depends on how many type errors mypy surfaces)

---

### PR-C-11: Add file size and extension limits to `read_file` tool

**Problem:** The `read_file` MCP tool (`mcp_server.py`) has no file size limit and no extension whitelist. A caller could request extremely large files or binary files, causing memory issues or returning binary garbage.

**Fix:**
1. Add a max file size constant (e.g., 1MB)
2. Add an allowlist of readable extensions (e.g., `.txt`, `.md`, `.py`, `.json`, `.yaml`, `.yml`, `.toml`, `.cfg`, `.ini`, `.sh`, `.bash`)
3. Return a clear error for oversized or disallowed files

**Files:** `src/engram_core/mcp_server.py`

**Risk:** Low — adds safety bounds, rejects oversized/disallowed files cleanly

**Complexity:** ⭐⭐ (medium)

---

### PR-C-12: Refactor `_apply_tool_tier()` to avoid FastMCP introspection

**Problem:** `mcp_server.py:108-124` uses `getattr(tool_manager, "_tools", None)` introspection to remove non-Tier-1 tools. This is fragile — FastMCP internals could change.

**Fix:**
1. Track registered tools in a module-level set
2. Use the FastMCP public API if available, or use a version check
3. Document the FastMCP version compatibility constraint

**Files:** `src/engram_core/mcp_server.py`

**Risk:** Medium — could break tool tier filtering if not done carefully

**Complexity:** ⭐⭐⭐ (higher — requires understanding FastMCP public API)

---

## Priority 4: Architecture & Design (Higher-Effort)

### PR-C-13: Split `mcp_server.py` into tool-category modules

**Problem:** `mcp_server.py` is 1411 lines — too large for a single module. Mixing tool definitions, middleware, auth, telemetry, and transport logic makes it hard to navigate and test.

**Fix (phased):**
1. Extract identity tools to `_tools_identity.py`
2. Extract knowledge tools to `_tools_knowledge.py`
3. Extract project tools to `_tools_projects.py`
4. Extract system tools (audit, import/export) to `_tools_system.py`
5. Keep `mcp_server.py` as the thin registration layer and SSE/stdio transport bootstrapper

**Files:** `src/engram_core/mcp_server.py` → new `_tools_*.py` modules

**Risk:** High — requires restructuring; must maintain all 43 tool registrations

**Complexity:** ⭐⭐⭐⭐ (high — major refactor, needs full test suite run afterward)

**Note:** This is a significant undertaking and should be considered v4.0 material.

---

### PR-C-14: Add `mypy` baseline and fix type errors

**Problem:** No type checking currently enforced. The codebase has type annotations but they haven't been validated against mypy.

**Fix:**
1. Install mypy and run `uv run mypy src --strict` (or with minimal config to start)
2. Create a mypy baseline file to track existing errors
3. Fix new errors introduced going forward

**Files:** `pyproject.toml`, potentially `.mypy.ini` or `[tool.mypy]` config

**Risk:** Low — tooling addition

**Complexity:** ⭐⭐ (medium — unknown number of pre-existing type errors)

---

## Summary Table

| ID | Title | Category | Risk | Complexity |
|---|---|---|---|---|
| PR-C-01 | Fix stale test count in README & comparison.md | docs | None | ⭐ |
| PR-C-02 | Update CONTRIBUTING.md telemetry claim | docs | None | ⭐ |
| PR-C-03 | Fix telemetry roadmap Phase 2 version label | docs | None | ⭐ |
| PR-C-04 | Refresh architecture.md line counts | docs | None | ⭐ |
| PR-C-05 | Fix core.py line count in README | docs | None | ⭐ |
| PR-C-06 | Validate dict keys in telemetry payload | telemetry | Medium | ⭐⭐ |
| PR-C-07 | Fix wrap_up_session tracking order bug | telemetry | Low | ⭐⭐ |
| PR-C-08 | Enforce allowlist for ENGRAM_TOOLS env var | validation | Low | ⭐ |
| PR-C-09 | Fix demos/cross_tool_demo.py lint violations | lint | None | ⭐ |
| PR-C-10 | Add mypy to dev deps + pre-commit/CI | tooling | Low | ⭐⭐ |
| PR-C-11 | Add size/extension limits to read_file tool | security | Low | ⭐⭐ |
| PR-C-12 | Refactor _apply_tool_tier() to avoid introspection | code quality | Medium | ⭐⭐⭐ |
| PR-C-13 | Split mcp_server.py into tool-category modules | architecture | High | ⭐⭐⭐⭐ |
| PR-C-14 | Add mypy baseline and fix type errors | tooling | Low | ⭐⭐ |