# v3.40 First-Run Confidence And Visual Trust Design

**Goal:** Make a new user feel, within five minutes, that piia-engram is installed, connected, local, inspectable, and under their control.

**Context:** v3.37-v3.39 closed several adoption gaps: a universal `piia-engram-mcp` entry point, bounded GUI MCP launch probes in `engram doctor`, local workflow visibility (`engram sessions` / `engram review`), and terminal encoding diagnostics. The next product step should not add another memory primitive. It should reduce first-run doubt: "Did it install?", "Is my GUI client actually connected?", "Where is my data?", "What can AI read?", and "Is this mojibake a display issue or corrupted storage?"

## Scope

v3.40 should focus on user-facing confidence surfaces:

- A concise status command or mode for non-expert users.
- A local, generated HTML status page that can be opened without a web server.
- A short first-run verification script that exercises the recommended install and MCP entry paths.
- Documentation that makes the Windows and GUI-client path obvious without burying users in implementation details.

The work should build on existing doctor/session/review primitives instead of inventing a new runtime subsystem.

## User Stories

1. As a new Windows user, I can install piia-engram and run one command that tells me whether the CLI, MCP entry point, storage root, and terminal encoding look healthy.
2. As a GUI MCP user, I can see which client configs are present, which entry style they use, and whether the configured entry can launch.
3. As a privacy-conscious user, I can open a local page and see where data lives, what recent sessions were saved, how many staged memories need review, and whether telemetry is off or configured.
4. As an existing user with suspected mojibake, I can distinguish terminal display risk from real stored-data corruption before running any repair.

## Proposed Interface

### CLI

Add one of these shapes after implementation review:

- `engram status`
- or `engram doctor --user-facing`

The command should print a small, stable summary:

```text
Engram status
CLI package: ok (3.40.0)
MCP entry: ok (piia-engram-mcp --help)
Storage: ok (~/.engram)
Terminal encoding: ok (UTF-8)
Knowledge: 186 lessons, 102 decisions, 0 staged items
Telemetry: off
Next step: run `engram review` when staged items appear
```

The exact command name can be finalized during implementation. Prefer `engram status` if the codebase already has a clean CLI command registration path; prefer `doctor --user-facing` if adding another top-level verb creates unnecessary surface area.

### Local HTML Status Page

Generate a local static file, for example:

```text
~/.engram/reports/status.html
```

The page should be readable without a server and should not include secret memory bodies by default. It should show:

- Installed package version and CLI path.
- Storage root and data file presence.
- Recent session count and last session timestamp.
- Staged knowledge count, not full sensitive content.
- MCP client config presence and entry style.
- Terminal encoding diagnosis.
- Telemetry state.
- Links or commands for `engram doctor`, `engram review`, and `engram sessions`.

Use restrained visual design. This is an operational trust surface, not a landing page.

## Non-Goals

- No hosted dashboard.
- No cloud login.
- No automatic GUI client editing beyond existing setup wizard behavior.
- No repair of stored data without explicit backup and apply confirmation.
- No body-level memory export in the status page.
- No public marketing redesign.

## Data Safety

The status page must default to metadata, counts, and health signals. It must not render raw lesson bodies, decision reasoning, playbook steps, audit log details, or identity-card bodies unless an explicit future opt-in flag is designed and reviewed.

For encoding repair, v3.40 should preserve the current dry-run-first posture:

- detect
- summarize
- backup
- apply only on explicit command
- write a repair report

## Acceptance Criteria

- A new user can run the first-run verification command from the README and get an actionable status summary.
- The status surface works on Windows with `PYTHONIOENCODING=utf-8` and with a UTF-8 terminal where `PYTHONIOENCODING` is unset.
- The status page is static HTML and contains no raw high-sensitivity memory bodies by default.
- Existing `engram doctor`, `engram sessions`, `engram review`, and MCP server behavior do not regress.
- Targeted tests cover command output, status-page redaction, encoding diagnostics, and MCP entry status integration.

## Review Plan

Before implementation, run a read-only Claude audit of v3.39.1 install and GUI entry paths. Treat any P0/P1 findings as blockers for v3.40 implementation. Codex should implement fixes and the v3.40 plan only after reading that report.
