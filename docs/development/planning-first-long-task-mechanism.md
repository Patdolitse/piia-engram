# Planning-First Long Task Development Mechanism

Status: optional project-development guidance

Scope: Piia Engram development tasks that are too large for a single focused
change and need repeated planning, implementation, review, and verification.

## Purpose

Use this mechanism to keep long workstreams useful and bounded. It is meant to
prevent a common failure mode in AI-assisted development: continuing to add
rules, boundary documents, scorecards, self-checks, or process artifacts after
the core product problem has stopped moving forward.

This is not a new product feature, release gate, or permission to perform public
maintenance. It is a lightweight way to plan before execution and verify before
claiming completion.

## When To Use

Use this mechanism when a task has most of these traits:

- It spans multiple files, tests, or documentation surfaces.
- It may require several implementation and review slices.
- The broad direction is already approved, but details can be handled by the
  development agents.
- There is meaningful risk of scope drift, documentation bloat, or overbuilding.
- The final answer needs evidence rather than a progress narrative.

Skip it for simple one-shot questions, low-risk single-file edits, or commands
whose output can be reported directly.

## Planning Packet

Before execution, write a short packet with:

```text
objective:
non_goals:
allowed_scope:
forbidden_scope:
current_evidence:
expected_outputs:
verification:
stop_conditions:
commit_plan:
reviewer_role:
```

The packet should be short enough to use. If it becomes a large specification,
split the task or reduce scope.

## Role Modes

### Codex Main / Claude Review

Default for implementation, test repair, verification, security-sensitive
changes, release preparation, and commit closure.

Codex responsibilities:

- Read current state from the worktree before acting.
- Keep the task aligned with the planning packet.
- Implement the smallest useful change.
- Run verification and inspect the diff.
- Prepare commits only after evidence is available.

Claude responsibilities:

- Review the plan or final diff when useful.
- Return concise `PASS`, `BLOCK`, or must-fix findings.
- Challenge scope drift, weak evidence, missed risks, and documentation bloat.

### Claude Main / Codex Review

Optional for broad but bounded work such as documentation normalization,
mechanical test coverage, repeated checklist completion, or implementation
slices where the allowed files and expected outputs are explicit.

Claude responsibilities:

- Implement only the approved slice.
- Report changed files, tests run, failures, and residual risks.
- Stop if the task needs new scope, public action, or owner judgment.

Codex responsibilities:

- Provide the handoff packet.
- Verify the diff independently.
- Re-run relevant tests.
- Decide whether the result is acceptable, needs revision, or must stop.
- Own final commit closure.

If Claude times out twice, cannot provide a structured result, or expands beyond
the packet, return to Codex-main mode.

## Anti-Overdevelopment Guard

Do not add a new rule, boundary document, self-proof artifact, dashboard,
scorecard, or process file unless at least one is true:

- It replaces or simplifies an existing scattered mechanism.
- It is required by a concrete failing test, recurring bug, or owner request.
- It creates executable verification instead of only more explanation.
- It is the smallest discoverable reference future contributors need.

Useful development moves the product, tests, documentation accuracy, or user
trust forward. A process artifact that only makes the project look more governed
should not be added.

## Verification Before Completion

Before reporting a long task as complete:

- Run the targeted tests or checks named in the planning packet.
- Run `git diff --check`.
- Inspect `git status --short` and confirm only intended files changed.
- For code changes, add or update tests unless there is a clear reason not to.
- For docs-only changes, verify links, terminology, and public/private wording.
- State any skipped verification directly in the final report.

## Stop Conditions

Stop and report partial progress if the next useful step requires:

- Public issue, PR, release, tag, registry, or package action.
- Credential, token, or account access.
- A change to project versioning or release promises.
- A private/public boundary decision that is not already approved.
- A scope expansion that would add process without improving the core objective.
- A situation where tests cannot be made green without changing intended
  behavior.

Stopping here is part of the mechanism. It prevents progress theater and keeps
the project focused on real product value.

## Outcome Standard

A successful long task leaves:

- A clearer product or engineering state than before.
- Evidence that the completed slice works.
- No unnecessary new process surfaces.
- A clean, understandable diff.
- A next step that is either concrete and owner-free, or explicitly owner-gated.
