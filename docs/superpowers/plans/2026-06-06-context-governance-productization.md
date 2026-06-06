# Context Governance Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the six-level post-A/B Engram backlog locally: release hygiene, recall governance integration coverage, context-governance proposal surfaces, defensive safe-context tests, a facade module, and user-facing documentation.

**Architecture:** Keep public actions locked. Preserve the A-list tool-surface discipline by avoiding four separate new MCP tools; expose B-list proposal helpers through one advanced, read-only, proposal-only surface backed by a `context_governance` facade. All generated artifacts remain local drafts/proposals and never mutate stored knowledge.

**Tech Stack:** Python, pytest, FastMCP wrappers in `src/piia_engram/mcp_server.py`, local Markdown docs, existing Engram governance classes.

---

### Task 1: Release Hygiene H1/H2

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`
- Modify: `docs/public-facts.json`
- Create: `docs/release-intent-vnext.md`

- [ ] Update `[Unreleased]` in both changelogs with the three local commits after v3.51.2.
- [ ] Refresh public facts with the current local test count and collection count.
- [ ] Add a local-only release-intent note saying the next release is pending owner confirmation.
- [ ] Run public fact/trust/positioning guards.

### Task 2: G2 Recall Governance Integration Test

**Files:**
- Modify: `tests/test_recall_service.py` or create a focused MCP-level test.

- [ ] Add a test that uses the same store with governance disabled/enabled and proves role-scoped recall filtering only applies when governance is enabled.
- [ ] Run the recall tests.

### Task 3: E1 Facade and G1 Unified Proposal Surface

**Files:**
- Create: `src/piia_engram/context_governance.py`
- Modify: `src/piia_engram/mcp_server.py`
- Modify: `tests/test_write_gate_matrix.py` indirectly through the governance matrix if a tool is added.
- Create: `tests/test_context_governance.py`
- Modify: `tests/test_mcp_tool_surface_rationalization.py`
- Modify: public count docs if the MCP count changes.

- [ ] Ask external reviewer whether one consolidated advanced MCP surface is acceptable after A-list rationalization.
- [ ] Add a pure facade that delegates to `safe_context`, `freshness_conflict_resolver`, `context_replay`, and `external_evidence_page`.
- [ ] Add one advanced MCP tool, classified as `read`, that returns proposal-only outputs for selected modes.
- [ ] Keep the new tool out of Tier-1 and out of `glama.yaml` unless a later public directory decision says otherwise.
- [ ] Update tool-count docs and tests if the total becomes 84/17/67.

### Task 4: E3 Safe Context Defensive Tests

**Files:**
- Modify: `tests/test_safe_context.py`

- [ ] Add parameterized/property-style tests with generated payloads proving redaction removes secret markers and trim respects `max_chars`.
- [ ] Fix implementation only if the tests expose a real leak or over-budget result.

### Task 5: E2 User Documentation

**Files:**
- Create: `docs/context-governance.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `.publishallow`
- Modify: `tests/test_public_positioning.py`

- [ ] Document what context-governance proposals do and do not do.
- [ ] Explicitly state that replay packets and evidence pages are local drafts and require owner confirmation before publishing.
- [ ] Link the doc from README surfaces without marketing overclaims.
- [ ] Add positioning tests for the doc.

### Task 6: Final Review, Verification, and Commit

**Files:**
- All touched files.

- [ ] Ask Claude for final architecture/release-risk review in English.
- [ ] Run focused tests, public guards, and full pytest.
- [ ] Commit locally only.
- [ ] Save Engram project snapshot and wrap up the session.

---

Self-review:

- Covers all six requested levels: H1, H2, G2, G1, E3, E1, E2.
- Keeps no-public-action rule explicit.
- Avoids four-tool surface expansion by choosing one consolidated advanced proposal surface unless external review rejects it.
- Leaves release/push/tag/PyPI/MCP Registry for later owner confirmation.
