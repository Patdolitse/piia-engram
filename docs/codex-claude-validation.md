# Codex + Claude Validation Workflow

This workflow records the local collaboration pattern used for Engram release-candidate work.

## Roles

- **Codex implementation owner**: reads the repo, makes scoped code and documentation changes, runs local tests, prepares evidence, and creates local commits.
- **Codex subagent reviewers**: perform read-only or bounded review tasks from different angles before the final commit.
- **Claude Code acceptance reviewer**: performs final independent acceptance after Codex fixes issues found by Codex subagents.

## Safety Rules

- Do not push, tag, publish, upload, merge, or update public registries without explicit user approval.
- Do not overwrite or restore live `~/.engram` data without explicit user approval.
- Claude Code must not delete or rewrite local documents unless Codex explicitly authorizes that operation for the current task.
- Claude acceptance should default to read-only tools: `Read`, `Glob`, `Grep`.
- If Claude needs to run commands, grant the narrowest tool permission and set a bounded timeout. Treat timeouts as inconclusive, not as PASS.
- Use ASCII prompts or UTF-8 prompt files for Claude CLI automation on Windows to avoid stdin mojibake.

## Recommended Acceptance Shape

Codex should provide Claude with:

- workspace path;
- exact review scope;
- forbidden actions;
- test evidence already collected by Codex;
- specific PASS/FAIL criteria;
- required output headings.

Claude should return:

- `VERDICT: PASS` or `VERDICT: FAIL`;
- findings with file references when applicable;
- remaining risks;
- release blocker yes/no.

## Release-Candidate Gate

Before a release candidate is considered ready for user approval:

- full pytest suite must pass;
- sanitize check must return `high=0`;
- publish allowlist must pass;
- package build and `twine check` must pass;
- Codex subagent review must be PASS;
- Claude acceptance review must be PASS;
- release evidence must record whether eval, negative-control, and field-assertion gates are applicable.
