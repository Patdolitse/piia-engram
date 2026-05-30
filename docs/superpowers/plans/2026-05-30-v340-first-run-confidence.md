# v3.40 First-Run Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development when independent tasks can run in parallel, or superpowers:executing-plans for sequential implementation. Claude should be used for read-only review/audit when the task touches install, security, encoding, or release confidence.

**Goal:** Deliver a first-run confidence surface for piia-engram: a concise user-facing status command, a local redacted status page, and a verification path that proves the recommended CLI/MCP entry points work.

**Architecture:** Reuse existing `doctor`, `sessions`, `review`, setup wizard entry classification, and encoding diagnostic helpers. Add thin aggregation/reporting layers rather than new persistence formats.

**Tech Stack:** Python standard library, existing CLI command registration, existing HTML report style where practical, pytest.

---

## Task 0: Consume Install/GUI Audit

- [ ] Read the latest read-only install/GUI audit report if one exists.
- [ ] Classify findings as blockers vs follow-ups.
- [ ] Fix P0/P1 install, README, GUI entry, or encoding issues before starting new v3.40 surface work.
- [ ] Record accepted decisions or lessons in Engram.

Verification:

```bash
python -m pytest tests/test_setup_wizard.py tests/test_packaging.py -q
```

---

## Task 1: Define Status Aggregation Contract

Files likely touched:

- `src/piia_engram/cli.py`
- `src/piia_engram/setup_wizard.py`
- possibly a new small module such as `src/piia_engram/status_report.py`
- tests under `tests/`

Steps:

- [ ] Identify existing helpers that already compute version, root, config status, encoding status, staged counts, recent sessions, telemetry status.
- [ ] Define a structured internal status dict with stable keys.
- [ ] Keep raw memory bodies out of the dict by default.
- [ ] Add tests that prove the status dict redacts bodies and is deterministic enough for CLI/HTML rendering.

Verification:

```bash
PYTHONPATH=src python -m pytest tests/test_setup_wizard.py -k status -q
```

---

## Task 2: Add User-Facing Status CLI

Decision point:

- Prefer `engram status` if CLI command registration stays small.
- Use `engram doctor --user-facing` only if a new verb would duplicate too much doctor plumbing.

Steps:

- [ ] Add the command.
- [ ] Print concise OK/WARN/FAIL lines.
- [ ] Include next-action hints only when useful.
- [ ] Avoid body dumps, stack traces, and noisy internal implementation labels.
- [ ] Add tests for healthy, warning, and staged-knowledge scenarios.

Verification:

```bash
PYTHONPATH=src python -m piia_engram.setup_wizard status
PYTHONPATH=src python -m pytest tests/test_setup_wizard.py -q
```

---

## Task 3: Add Local Redacted Status Page

Steps:

- [ ] Add an HTML renderer for status metadata.
- [ ] Write to `~/.engram/reports/status.html` or an explicit output path.
- [ ] Include package/version, storage root, MCP entry status, terminal encoding, session summary, staged count, telemetry state.
- [ ] Do not include lesson bodies, decision reasoning, playbook steps, audit log detail, identity-card content, or secrets.
- [ ] Add tests that seed sensitive strings and assert they do not appear in the HTML.

Verification:

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

---

## Task 4: First-Run Verification Script/Docs

Steps:

- [ ] Update README and README.zh-CN with one short first-run verification path.
- [ ] Include Windows-friendly commands using the canonical Python runtime only where local developer docs require it; public README should keep general commands simple.
- [ ] Add a docs page or section that explains: install, connect GUI, run status/doctor, review staged memories.
- [ ] Ensure docs do not mention stale channel states or internal audit process details.

Verification:

```bash
python scripts/release_sanitize_check.py --internal
python scripts/check_publish_allowlist.py
```

---

## Task 5: Independent Review And Release Gate

Steps:

- [ ] Run targeted tests.
- [ ] Run full tests.
- [ ] Ask Claude for read-only review of the v3.40 diff.
- [ ] Fix findings.
- [ ] Update release evidence only if preparing a release.
- [ ] Do not push, tag, release, or merge without explicit user authorization.

Verification:

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

---

## Open Questions

- Should the public command be `engram status` or `engram doctor --user-facing`?
- Should the HTML page be generated automatically by `status`, or only by `engram status --html`?
- Should staged knowledge show only counts, or include redacted titles for low-sensitivity items?

Recommended defaults:

- Use `engram status`.
- Generate text by default; HTML only with `--html`.
- Show counts only in v3.40; consider redacted titles later after governance review.
