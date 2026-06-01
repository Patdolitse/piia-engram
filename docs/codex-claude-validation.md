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
- Use English for Codex-to-Claude prompts and Claude-to-Codex responses. Codex translates the final result for the user.
- Use ASCII-only Markdown prompt files for Claude CLI automation on Windows. Prefer `claude -p < prompt.md` or an equivalent file-based input path over long inline PowerShell strings.
- Do not ask one Claude call to audit a broad multi-file subsystem unless the scope is intentionally small. Split broad reviews into narrow prompts and reconcile the results in Codex.
- Keep each Claude prompt explicit about allowed tools. Use no-tools reasoning for planning and read-only file tools for acceptance review unless a command run is truly needed.

## Model and Effort Policy

- Planning, product direction, and architecture discussion: use the strongest available Claude reasoning model with high or extra effort.
- Testing and acceptance audit: use a fast high-effort Claude model with read-only tools by default.
- Routine wording, summarization, and status notes: use the lowest model that can preserve accuracy.
- If a Claude run times out, do not retry the same broad prompt. First reduce scope, switch to a prompt file if stdin or quoting was involved, and only then retry.

## Recommended Acceptance Shape

Codex should provide Claude with:

- workspace path;
- exact review scope;
- forbidden actions;
- test evidence already collected by Codex;
- specific PASS/FAIL criteria;
- required output headings.
- a hard instruction to report `INCONCLUSIVE` when the evidence is insufficient or a timeout/tool limit prevents review.

Claude should return:

- `VERDICT: PASS` or `VERDICT: FAIL`;
- `VERDICT: INCONCLUSIVE` when the review did not actually complete;
- findings with file references when applicable;
- remaining risks;
- release blocker yes/no.

## Prompt File Template

Use this shape for repeatable local acceptance reviews:

```text
# Claude Acceptance Review

Workspace: E:/Personal Intelligence Identity Asset/engram

Role: independent acceptance reviewer.

Allowed actions:
- Read files.
- Search files.
- Do not edit, delete, commit, push, publish, tag, upload, or update registries.
- Do not rewrite local documents unless Codex explicitly authorizes it in this prompt.

Scope:
- Review only the listed files and behavior.
- Treat all other areas as out of scope unless they create a direct blocker.

Evidence from Codex:
- <commands and results>

PASS criteria:
- <specific invariants>

Return exactly:
- VERDICT: PASS | FAIL | INCONCLUSIVE
- FINDINGS:
- RISKS:
- RELEASE BLOCKER: yes | no
```

For wide reviews, create one prompt per independent domain. Codex owns final
integration: it compares Claude findings against local tests, applies fixes,
reruns evidence, and then asks Claude for a final narrow acceptance pass.

## Release-Candidate Gate

Before a release candidate is considered ready for user approval:

- full pytest suite must pass;
- sanitize check must return `high=0`;
- publish allowlist must pass;
- package build and `twine check` must pass;
- Codex subagent review must be PASS;
- Claude acceptance review must be PASS;
- release evidence must record whether eval, negative-control, and field-assertion gates are applicable.
