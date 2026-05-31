# Trust Resume Portability Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the next Engram foundation package: safe lessons recovery planning, a stronger `get_resume_brief` hero experience, trust-mode metadata primitives, and cross-client configuration portability/integrity checks.

**Architecture:** Keep this as four reviewable stages. Stage 1 is metadata-only and never restores live `lessons.json` without explicit user approval. Stages 2-4 are repo changes with focused tests and local commits; no push, tag, publish, registry update, or live restore is allowed.

**Tech Stack:** Python 3.12, pytest, PowerShell, MCP tool layer, Engram JSON store, Codex subagents, Claude Code read-only acceptance.

---

## Boundaries

- Do not publish, push, tag, merge, upload, or update registries.
- Do not overwrite live `~/.engram/knowledge/lessons.json` without explicit user approval.
- Do not print lesson bodies in recovery reports.
- Keep docs public-safe; avoid real local usernames and private absolute paths in committed fixtures.

## Stage 1: Lessons Recovery And Retention Dry-Run

**Files:**
- Modify: `src/piia_engram/recovery.py`
- Modify: `src/piia_engram/setup_wizard.py`
- Test: `tests/test_recovery.py`
- Test: `tests/test_setup_wizard.py`

- [x] Add a metadata-only overlap/union analyzer for recovery candidates.
- [x] Add an overflow recommendation that refuses blind active merge when union exceeds `MAX_KNOWLEDGE_ENTRIES`.
- [x] Expose the plan through `engram recover-json lessons`.
- [x] Add tests proving no content text appears in the report.
- [x] Add tests proving archived old-only entries are not silently promoted.

## Stage 2: Resume Brief Hero Experience

**Files:**
- Modify: `src/piia_engram/contexts.py`
- Modify: `src/piia_engram/mcp_server.py`
- Test: `tests/test_resume_brief_v3_30.py`
- Docs: `docs/cross-tool-guide.md`, `README.md`, `README.zh-CN.md`

- [x] Audit current `get_resume_brief` output sections.
- [x] Add a concise handoff header with project, last activity, next action, and trust note.
- [x] Ensure token-budget trimming keeps identity/project/last activity before lower-priority sections.
- [x] Add tests for the 30-second handoff shape.
- [x] Add docs showing Claude Code to Codex to Cursor/Windsurf handoff.

## Stage 3: Trust Mode Foundation

**Files:**
- Modify: `src/piia_engram/storage.py`
- Modify: `src/piia_engram/core.py`
- Modify: `src/piia_engram/context.py`
- Test: `tests/test_storage.py`, `tests/test_core.py`, `tests/test_reconcile.py`
- Docs: `SECURITY.md`, `docs/trust.md`

- [x] Add canonical memory states: `staging`, `verified`, `rejected`, `deprecated`.
- [x] Preserve backward compatibility with existing `tier` and `status` fields.
- [x] Add risk/provenance helpers for memory entries.
- [x] Ensure AI-originated extracted lessons default to staging.
- [x] Add tests for risky memory metadata and approval boundaries.

## Stage 4: Config Portability And Integrity

**Files:**
- Modify: `src/piia_engram/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`
- Docs: `docs/cross-tool-guide.md`, `docs/trust.md`

- [x] Add a config integrity report with MCP config, Claude hook, and shared instruction hashes.
- [x] Add host instruction portability checks for `AGENTS.md`, `CLAUDE.md`, Cursor rules, Copilot instructions, and Windsurf rules.
- [x] Keep doctor output public-safe and readable on Windows.
- [x] Add tests that config checks do not rewrite user files.
- [x] Add docs for interpreting the trust/config report.

## Final Verification

- [x] Run focused tests for each stage.
- [ ] Run `pytest tests/ -q`.
- [ ] Run publish allowlist.
- [ ] Run internal sanitize.
- [ ] Run build + `twine check`.
- [ ] Ask Codex subagents for engineering/security review.
- [ ] Ask Claude Code for final read-only acceptance.
- [ ] Write a desktop completion report.
- [ ] Commit locally only; do not push.
