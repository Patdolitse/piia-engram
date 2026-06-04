# v3.48.3 Import Candidate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local v3.48.3 release-candidate checkpoint by modularizing import/export, adding safer import conflict preview, and exposing a CLI import preview/apply flow without publishing.

**Architecture:** Move full-backup import/export helpers out of `core.py` into a focused mixin while preserving the existing `Engram.export_all()` / `Engram.import_all()` API. Extend dry-run planning to surface same-key divergent knowledge as metadata-only conflicts, then add a CLI wrapper that defaults to preview and requires explicit apply confirmation.

**Tech Stack:** Python, pytest, FastMCP wrapper, local JSON storage, Engram governance, PowerShell on Windows.

---

### Task 1: Extract Import/Export Module

**Files:**
- Create: `src/piia_engram/import_export.py`
- Modify: `src/piia_engram/core.py`
- Test: `tests/test_core.py`

- [x] Move `export_all`, `import_all`, and import-planning helpers into `ImportExportMixin`.
- [x] Keep method names and return payloads backward-compatible.
- [x] Update `Engram` inheritance to include `ImportExportMixin`.
- [x] Run targeted import/export tests.

### Task 2: Metadata-Only Knowledge Conflict Preview

**Files:**
- Modify: `src/piia_engram/import_export.py`
- Modify: `tests/test_core.py`

- [x] Add failing tests for lessons with same summary but divergent detail and decisions with same question but divergent choice/reasoning.
- [x] Implement dry-run conflict metadata for divergent same-key knowledge.
- [x] Keep actual `merge=True` behavior non-destructive unless an explicit apply strategy is added later.
- [x] Run targeted tests.

### Task 3: CLI Import Preview/Apply

**Files:**
- Modify: `src/piia_engram/setup_wizard.py`
- Modify: `tests/test_setup_wizard.py`

- [x] Add `engram import <backup.json>` as preview by default.
- [x] Add `engram import <backup.json> --apply --yes` for mutation.
- [x] Add `--overwrite` option that maps to `merge=False` and still requires apply confirmation.
- [x] Return JSON when `--json` is passed.
- [x] Run CLI targeted tests.

### Task 4: Documentation And Public Truth

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/specs/multi-device-sync-sovereignty.md`
- Modify: `docs/architecture.md`
- Modify: `docs/public-facts.json`

- [x] Document CLI import preview/apply.
- [x] Keep public claims scoped to local import preview; no sync transport claim.
- [x] Update test counts after collect/full test evidence.
- [x] Run `scripts/check_public_fact_sync.py`.

### Task 5: Audit, Verify, Commit

**Files:**
- All changed files.

- [x] Ask Claude for read-only English diff audit.
- [x] Run targeted tests, `compileall`, `git diff --check`, MCP tool count, public-fact guard, and full pytest with `ENGRAM_DIR` cleared.
- [x] Commit locally only.
- [x] Save Engram decision/lesson/project snapshot.
