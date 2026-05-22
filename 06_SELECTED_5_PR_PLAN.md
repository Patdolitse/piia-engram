# Phase 6 — Selected 5 PR Plan

This document selects **5 PR candidates** from the backlog (`05_PR_CANDIDATES.md`) and defines execution plans for each. Selection criteria: clear problem statement, low/medium risk, well-scoped, independently mergeable, and grounded in the cross-AI evaluation report or code review findings.

---

## Selected Candidates

### PR-01: Fix stale test count in README.md and docs/comparison.md

**candidate_id:** PR-C-01  
**title:** Fix stale test count in README.md and docs/comparison.md  
**category:** Documentation Corrections  
**linked_issue:** Cross-AI report issue #5 (`docs/comparison.md:34`); cross-AI report issue #8 (`README.md:349`)  
**problem:** Two documentation files report incorrect metrics. `README.md:348` says "490 tests" and `docs/comparison.md:34` says "386 tests / 78% coverage". Actual count is **657 tests at 83% coverage** (v3.16.0).  
**proposed_solution:**  
- `README.md` line 348: `"490 tests"` → `"657 tests"`  
- `docs/comparison.md` line 34: `"386 tests, 78% coverage (v3.14.2)"` → `"657 tests, 83% coverage (v3.16.0)"`  
**target_files:** `README.md`, `docs/comparison.md`  
**test_plan:** Run `uv run pytest --co -q` locally to confirm 657 collected tests; grep README and comparison.md for old numbers to ensure no further stale entries remain.  
**risk_level:** None — purely numeric text corrections.  
**expected_diff_size:** ~4 lines changed across 2 files.  
**merge_likelihood:** Very high — pure documentation fix, no code impact, trivial review.  
**why selected:** Grounded in the cross-AI evaluation report. Easiest PR in the backlog with zero risk and immediate value (corrects misleading metrics shown to every new visitor).

---

### PR-02: Update CONTRIBUTING.md telemetry claim

**candidate_id:** PR-C-02  
**title:** Update CONTRIBUTING.md telemetry claim  
**category:** Documentation Corrections  
**linked_issue:** Cross-AI report issue #4 (`CONTRIBUTING.md:28`)  
**problem:** `CONTRIBUTING.md:28` says "Engram does not send telemetry" — incorrect since v3.15.0 introduced opt-in telemetry. The claim directly misleads potential contributors.  
**proposed_solution:** Update the telemetry section in `CONTRIBUTING.md` to: (1) reflect the opt-in model, (2) point contributors to `docs/telemetry_roadmap.md` for details, and (3) remove the outdated "no telemetry" statement.  
**target_files:** `CONTRIBUTING.md`  
**test_plan:** Read the updated CONTRIBUTING.md and verify the telemetry section is internally consistent with `docs/telemetry_roadmap.md`. No code tests needed.  
**risk_level:** None — documentation only.  
**expected_diff_size:** ~5–10 lines, localized to one section.  
**merge_likelihood:** Very high — factual correction, trivial review.  
**why selected:** Also grounded in the cross-AI evaluation report. Corrects a statement that could deter contributions from security-conscious developers who otherwise would engage. Paired naturally with PR-01 (both are doc corrections, can even be merged as one PR if reviewer prefers).

---

### PR-03: Validate dictionary keys in telemetry payload

**candidate_id:** PR-C-06  
**title:** Validate dictionary keys in telemetry payload  
**category:** Telemetry / Security Hardening  
**linked_issue:** Cross-AI report issue #1 (`telemetry.py:139-155`); blocks Phase 2 network telemetry work  
**problem:** `_validate_payload` recursively validates string values but not dictionary keys. Tool names are dict keys and could theoretically carry arbitrary content (paths, natural language). While currently not exploitable (tool names are fixed string literals), this blocks the future Phase 2 network telemetry work where untrusted keys could be passed.  
**proposed_solution:**  
1. Add key validation to `_validate_payload` — keys must be alphanumeric + underscore, max 64 chars  
2. Enforce a static allowlist of known MCP tool names in the tracker  
3. Update `test_telemetry.py` with a dict-key test case  
**target_files:** `src/engram_core/telemetry.py`, `tests/test_telemetry.py`  
**test_plan:** Run `uv run pytest tests/test_telemetry.py -v` before and after; add a new test case that passes a dict with a non-alphanumeric key and asserts it raises `ValidationError`.  
**risk_level:** Medium — changes validation logic; regression possible if allowlist is too restrictive. Mitigation: allowlist covers all 43 current tool names, and unknown keys from Phase 2 will be explicitly validated.  
**expected_diff_size:** ~30–50 lines (validation logic + allowlist + test).  
**merge_likelihood:** High — explicitly recommended in the cross-AI evaluation report as a prerequisite for Phase 2. Clear benefit, clear implementation, reviewer can verify against the allowlist.  
**why selected:** The highest-severity telemetry issue in the cross-AI report. Directly unblocks the Phase 2 roadmap (network telemetry). The allowlist approach is simple and auditable.

---

### PR-04: Fix `wrap_up_session` tracking order bug

**candidate_id:** PR-C-07  
**title:** Fix `wrap_up_session` tracking order bug  
**category:** Telemetry / Correctness  
**linked_issue:** Cross-AI report issue #3 (`mcp_server.py:1216-1235`)  
**problem:** `wrap_up_session` calls `flush()` to write stats to the log, then records `_track("wrap_up_session")`. This means the session-ending call itself is excluded from the flushed stats — the usage log never records the final `wrap_up_session` invocation.  
**proposed_solution:** Move `_track("wrap_up_session")` to immediately **before** the `flush()` call in `wrap_up_session`.  
**target_files:** `src/engram_core/mcp_server.py` (lines ~1216–1235)  
**test_plan:** Run `uv run pytest tests/ -v` to ensure no regression; manually inspect that `_track` call order is now before flush by reviewing the diff.  
**risk_level:** Low — logic fix with no data loss; the tracking call is simply reordered.  
**expected_diff_size:** ~2–4 lines (one `_track` call moved).  
**merge_likelihood:** Very high — one-line logical fix, clearly correct, backed by the cross-AI report.  
**why selected:** Grounded in the cross-AI report. Minimal diff with a clear correctness benefit — every session end will now be correctly attributed in usage statistics.

---

### PR-05: Add file size and extension limits to `read_file` tool

**candidate_id:** PR-C-11  
**title:** Add file size and extension limits to `read_file` tool  
**category:** Security Hardening  
**linked_issue:** Code review observation D (`mcp_server.py:read_file`)  
**problem:** The `read_file` MCP tool has no file size limit and no extension whitelist. A caller could request extremely large files (causing memory issues) or binary files (returning garbage or exposing sensitive binary data).  
**proposed_solution:**  
1. Add a `MAX_FILE_SIZE = 1_048_576` (1MB) constant  
2. Add an allowlist of readable extensions: `.txt`, `.md`, `.py`, `.json`, `.yaml`, `.yml`, `.toml`, `.cfg`, `.ini`, `.sh`, `.bash`, `.html`, `.css`, `.js`, `.ts`, `.txt`  
3. Return a clear error message for oversized or disallowed files (e.g., `"File exceeds 1MB limit"` / `"Extension .exe not allowed"`)  
**target_files:** `src/engram_core/mcp_server.py`  
**test_plan:** Add a test in `tests/test_mcp_server.py` (or a new focused test file) that: (1) attempts to read a file >1MB and asserts an appropriate error, (2) attempts to read a `.bin` or `.exe` file and asserts it is rejected. Run full test suite with `uv run pytest tests/ -v`.  
**risk_level:** Low — adds safety bounds, rejects with clear messages; does not change behavior for valid requests.  
**expected_diff_size:** ~25–40 lines (constants + validation block + error handling + tests).  
**merge_likelihood:** High — addresses a real security gap identified in code review. The fix is self-contained and easy to verify.  
**why selected:** The only security-hardening item among the lower-complexity candidates. Closes a real attack surface in the MCP tool layer. Well-scoped and independently testable.

---

## Rejection Rationale: Non-Selected Top Candidates

### PR-C-03: Fix `docs/telemetry_roadmap.md` Phase 2 version label

**Reason for rejection:** While a factual correction (low risk, low effort), it is a **purely cosmetic** fix. The Phase 2 version label being wrong causes no confusion in practice — any reader consulting the actual code will see Phase 1 is current. By contrast, PR-02 (CONTRIBUTING.md telemetry) corrects a statement that actively misleads contributors about behavior. PR-C-03 is deprioritized but not discarded — it can be merged as a drive-by fix alongside PR-02 if a reviewer requests it.

### PR-C-04: Refresh architecture.md line counts

**Reason for rejection:** Stale line counts in `docs/architecture.md` are low-value cosmetic issues. The effort to re-count and update is trivial, but the practical impact is near-zero — line counts are rarely used by readers and become stale again quickly. Combined with PR-01 (which handles the README/comparison.md stale metrics), this is redundant coverage. Deferred to a future "docs refresh" PR.

### PR-C-05: Fix `core.py` line count in README

**Reason for rejection:** Same reasoning as PR-C-04 — a cosmetic line count fix with near-zero practical impact. One extra line (1088 → 1097) does not affect any behavior. Covered partially by PR-01's broader README refresh scope.

### PR-C-08: Enforce allowlist for `ENGRAM_TOOLS` env var

**Reason for rejection:** This is a legitimate improvement (env var validation), but it is **lower urgency** than the security gap in PR-05. The `ENGRAM_TOOLS` env var currently accepts any string and only checks `!= "core"` to enable all 43 tools — so an unknown value silently enables all tools, which is not dangerous but is ambiguous. PR-05's `read_file` lack of size/extension limits is a more direct security risk. PR-C-08 is a good candidate for a follow-up PR after these 5 are merged.

### PR-C-09: Fix `demos/cross_tool_demo.py` lint violations

**Reason for rejection:** Two ruff violations in a demo file — one import ordering and one unnecessary f-string. While correctable, this affects only a demo file, not production code. It does not appear in the cross-AI evaluation report and presents no security or correctness risk. Very low priority relative to the selected 5.

### PR-C-10: Add `mypy` to dev deps + pre-commit/CI

**Reason for rejection:** Adding type-checking tooling is valuable but **open-ended** — the outcome depends on how many type errors `mypy` surfaces, and fixing pre-existing type errors across 1411 lines of `mcp_server.py` could become a large, unbounded PR. This is better handled as a tracked task with a separate issue rather than as a single PR. A more surgical type-checking PR (e.g., fixing types in one module at a time) would be more mergeable.

### PR-C-12: Refactor `_apply_tool_tier()` to avoid FastMCP introspection

**Reason for rejection:** This is a **medium-complexity robustness improvement** that addresses a fragile pattern (getattr on FastMCP internals). However, it requires deep FastMCP public API research to implement safely, and the risk of breaking tool tier filtering is non-trivial. While valuable, it is not grounded in the cross-AI evaluation report and lacks the urgency of the selected 5 items. Deferred to a future architecture PR.

### PR-C-13: Split `mcp_server.py` into tool-category modules

**Reason for rejection:** This is explicitly labeled "v4.0 material" in the candidates doc. A 1411-line module split is a **major refactor** with high risk of breaking all 43 tool registrations. It requires a comprehensive test plan, a phased migration strategy, and likely a major version bump. Not appropriate as a single PR in this campaign.

### PR-C-14: Add `mypy` baseline and fix type errors

**Reason for rejection:** Same concern as PR-C-10 — this is an open-ended tooling task whose scope depends on how many pre-existing type errors exist. Without knowing the mypy baseline count, estimating the effort is impossible. Deferred to a dedicated tooling initiative.

---

## Summary Table

| # | ID | Title | Category | Risk | Diff Size | Merge Likelihood |
|---|---|---|---|---|---|---|
| 1 | PR-C-01 | Fix stale test count in README & comparison.md | docs | None | ~4 lines | Very High |
| 2 | PR-C-02 | Update CONTRIBUTING.md telemetry claim | docs | None | ~5–10 lines | Very High |
| 3 | PR-C-06 | Validate dictionary keys in telemetry payload | telemetry/security | Medium | ~30–50 lines | High |
| 4 | PR-C-07 | Fix `wrap_up_session` tracking order bug | telemetry | Low | ~2–4 lines | Very High |
| 5 | PR-C-11 | Add size/extension limits to `read_file` tool | security | Low | ~25–40 lines | High |

**Total estimated diff:** ~66–108 lines across 5 files.

**Order of attack:**  
1. **PR-01 + PR-02** (both zero-risk doc fixes; natural pairing)  
2. **PR-04** (two-line telemetry fix; quick win)  
3. **PR-03** (medium-complexity telemetry validation; needs more care)  
4. **PR-05** (security hardening; well-scoped, independently testable)