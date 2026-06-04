# Client Validation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Hermes/OpenClaw hand-written validation evidence into reusable, testable scaffolding while keeping public claims conservative.

**Architecture:** Add a pure `client_validation` evidence helper distinct from the simulated `continuity_harness`, then connect it to a thin CLI scaffold and public-safe documentation. Local reports stay Chinese; public summaries stay scrubbed and English-first bilingual.

**Tech Stack:** Python, pytest, PowerShell, Markdown docs, Engram client-validation runbook.

---

### Task 1: Evidence Helper

**Files:**
- Create: `src/piia_engram/client_validation.py`
- Test: `tests/test_client_validation.py`

- [x] Add run metadata, tool-location, evidence-layout, zero-pollution, and public-claim guard helpers.
- [x] Add tests for required metadata keys, evidence layout, zero-pollution pass/fail, Chinese markdown rendering, and OpenClaw claim boundaries.
- [x] Run `python -m pytest tests/test_client_validation.py -q`.

### Task 2: CLI Scaffold

**Files:**
- Create: `scripts/run_client_validation.py`

- [x] Add a CLI that creates a standard run directory and starter evidence files.
- [x] Keep it non-invasive: it does not run external clients or mutate live memory.

### Task 3: Documentation

**Files:**
- Modify: `docs/runbooks/agent-client-validation.md`
- Create: `docs/integrations/client-continuity-evidence.md`
- Create: `docs/specs/openclaw-live-agent-plan.md`
- Modify: `.publishallow`

- [x] Add harness usage to the runbook.
- [x] Add a public-safe bilingual evidence summary.
- [x] Add a plan-only OpenClaw live-agent validation spec.
- [x] Add new public docs to the publish allowlist.
