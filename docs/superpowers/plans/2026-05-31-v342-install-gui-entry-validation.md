# v3.42 Install And GUI Entry Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and harden the post-v3.41 new-user install, MCP entry, GUI configuration, Windows encoding, and doctor guidance experience without publishing.

**Architecture:** Use isolated temporary Engram roots and virtual environments so tests never mutate the user's real `~/.engram` or GUI configs. Convert every real finding into a narrow docs/code/test change, with release-grade verification but no push/tag/release.

**Tech Stack:** Python 3.12, pytest, PowerShell, piia-engram CLI/MCP entry points, GitHub/registry checks read-only only.

---

### Task 1: Baseline And Isolation

**Files:**
- Read: `pyproject.toml`
- Read: `.mcp/server.json`
- Read: `README.md`
- Create: `E:/Temp/engram-v342-validation/`

- [x] Confirm worktree branch and clean status.
- [x] Create temporary validation root at `E:/Temp/engram-v342-validation`.
- [x] Run targeted public positioning tests.
- [x] Run full test suite before modifying code.
- [x] Record baseline commands and results for the final report.

### Task 2: New-User Install Matrix

**Files:**
- Create: `E:/Temp/engram-v342-validation/venv/`
- Create: `E:/Temp/engram-v342-validation/install-matrix.md`

- [x] Create a fresh virtual environment.
- [x] Install `piia-engram==3.41.0` from PyPI with `--no-cache-dir`.
- [x] Verify import version, `piia-engram --help`, `engram --help`, `piia-engram-mcp --help`, and `python -m piia_engram.mcp_server --help`.
- [x] Run `piia-engram doctor` with `ENGRAM_DIR` set to a temporary root.
- [x] Capture any confusing output, Windows encoding warnings, stale-path leaks, or entry-point mismatch.

### Task 3: GUI Config And Doctor Readability

**Files:**
- Read: `src/piia_engram/setup_wizard.py`
- Read: `src/piia_engram/status_report.py`
- Read: `README.md`
- Read: `README.zh-CN.md`

- [x] Inspect current Claude Desktop, Cursor, Codex, and Windsurf config paths read-only.
- [x] Run doctor against real config read-only and classify every warning as expected, confusing, or actionable.
- [x] If output is misleading, write a failing test first, then implement the smallest fix.
- [x] If docs are misleading, update README/zh README or relevant docs with conservative wording.

### Task 4: Regression Tests

**Files:**
- Modify or create focused tests under `tests/`

- [x] Add tests only for confirmed behavior gaps.
- [x] For each code behavior change, watch the test fail before implementation.
- [x] Run the new targeted tests.
- [x] Run affected existing tests.

### Task 5: Verification And Audit

**Files:**
- Create: `<owner-desktop>/Engram_v3.42_Install_GUI_Validation_Report_2026-05-31.md`

- [x] Run full `pytest tests/ -q`.
- [x] Run `scripts/release_sanitize_check.py --internal --strict`.
- [x] Run `scripts/check_publish_allowlist.py`.
- [x] Run package build and `twine check` into `E:/Temp`.
- [x] Ask Codex subagent for read-only audit.
- [x] Ask Claude Code for final read-only acceptance.
- [x] Write the desktop report with matrix, fixes, tests, audits, remaining risks, and explicit no-publish status.

### Task 6: Completion State

**Files:**
- Read: git status
- Write: Engram memory

- [x] Keep all work local; do not push, tag, release, upload, or publish.
- [x] Commit locally only if changes are coherent and all gates pass.
- [ ] Record reusable lessons/decisions in Engram.
- [x] Leave final instructions for the next morning: what changed, how to review, and whether it is ready for a future release cycle.
