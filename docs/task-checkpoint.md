# Optional labelled task checkpoints

[中文](task-checkpoint.zh-CN.md)

Claude Code Stop and PreCompact hooks can optionally snapshot a small labelled
assistant task block before queuing the event. This feature is **off by default**.
Queue encryption does not enable it or constitute consent to capture conversation
text. Existing hook registration and client configuration are unchanged.

## Opt in

Set both variables in the environment inherited by the hook process:

```sh
export ENGRAM_HOOK_TASK_CHECKPOINT_V1=1
export ENGRAM_HOOK_TRANSCRIPT_ROOTS='["/absolute/transcripts"]'
```

PowerShell example using a directory you explicitly authorize:

```powershell
$env:ENGRAM_HOOK_TASK_CHECKPOINT_V1 = '1'
$env:ENGRAM_HOOK_TRANSCRIPT_ROOTS = '["D:/transcripts"]'
```

The roots variable is a JSON array of 1–16 absolute, local directories. Use your
actual transcript directory; an example path grants no access by itself. Roots
are never inferred from the event, project directory or client configuration.
Only regular `.jsonl` files inside those directories qualify. Network/device
paths, alternate streams, symlinks, junctions and containment escapes are refused.
An absent/invalid allowlist captures no task text. Only the exact opt-in value
`1` enables the feature; unset it to return to existing capture behavior. Already
queued snapshots are still processed and are not erased by disabling capture.

## Required assistant block

The latest assistant record must contain exactly one plain, unquoted, unfenced
block with all five labels, each exactly once. Separate the block from surrounding
prose with blank lines. Labels are case-sensitive; ASCII or full-width colons
are accepted. Do not split a checkpoint across assistant turns.

```text
Goal: Finish the sample feature
Constraint: Preserve the existing sample behavior
Risk: Await sample verification
Last failure reason: A sample assertion failed
Next: Run the sample verification
```

Equivalent Chinese labels are fixed:

| Label | Chinese label | Typed field |
| --- | --- | --- |
| Goal | 目标 | goal |
| Constraint | 约束 | constraint |
| Risk | 风险 | blocker |
| Last failure reason | 上次失败原因 | last_failure_reason |
| Next | 下一步 | next_action |

Chinese values are supported. Explicit `unknown`, `未知` and other documented
unknown forms (`Unknown`, `不详`, `未提供`, `不确定`, `N/A`, `n/a`) become null with an
omission code. Values longer than 240 Unicode characters are omitted, never
truncated. A block without any retained values has no task authority. Partial,
duplicate, conflicting, quoted or fenced groups are refused. No earlier assistant
task is substituted when the latest assistant record lacks a valid block. User,
tool and thinking content never supplies these fields.

## Storage and reading

The producer reads at most a 256 KiB transcript tail, discards an incomplete
leading boundary record, and rejects candidate records over 64 KiB. A trailing
incomplete record causes no capture. The validated `task_checkpoint.v1` object is
at most 8 KiB including metadata. Normalization and composed secret/PII checks
run before persistence, across complete values and field boundaries, and again
during preparation. Sensitive fields become null with a `privacy` omission code;
ambiguous cross-field candidates are dropped whole. Filters cannot
recognize every kind of private prose: only opt in for content you authorize.

The object travels in `payload.task_checkpoint` under `hooks/spool/`. Deferred
preparation freezes it with canonical text; retries do not reread the source to
repair or replace that snapshot. After an explicit `engram hooks drain --json`,
the canonical retained fields appear in `contexts/claude_code/<session>.md` and
as `task_checkpoint` in the adjacent `session_digest.v1` JSON sidecar. A valid
snapshot remains processable if the transcript changes or disappears, even below
the old activity-count threshold. Failed capture leaves existing metadata capture
in place when the deadline permits. Existing legacy missing-source retries remain.

**These are plaintext content surfaces.** Without queue protection, queued and
prepared objects are plaintext too. Optional [queue body protection](hook-queue-protection.md)
protects the queue/prepared body only: downstream session Markdown, digest JSON,
publication intermediates, relevant temporary/backup copies and authorized read
projections remain plaintext. Retention, deletion and secure erasure are not
provided by this feature.

Native resume briefs use the typed goal, constraint, blocker, last failure reason
and next action before background excerpts, with `claude_code` provenance and
`earlier session record; unreviewed; no approval or action authority`. Existing
project identity, revision and freshness arbitration still applies; a newer
canonical checkpoint can win. This is provenance, not authenticated client identity.

Task snapshots do not create knowledge, review proposals or approvals, update
canonical project state, or feed quick context/daily archives. Strict-mode
knowledge eligibility is unchanged. The separate `hook_content_digest_v2`
preference remains an independent feature using its own available transcript
input; it is not enabled by task capture and never derives from the frozen object.
Capture outcomes and processing receipts are separate: a receipt means processed,
not complete, reviewed or adopted. Direct automatic continuity still requires a
consumer; these hooks do not drain their own queue.

## Timing and PreCompact limitation

Extraction, including lazy imports, gets at most
`min(200 ms, remaining application deadline - 525 ms)` inside the existing
one-second application deadline. The reserve protects queue sealing and
diagnostics. Startup and final filesystem publication can add wall time. A timed
out worker can finish only in memory, never publish later. Protection failures
still refuse publication without a plaintext downgrade. Hook failure remains
fail-soft for the host and fail-closed for task content; no model is called.

**PreCompact is best effort.** Existing asynchronous registration can let the host
rewrite a transcript before the hook starts reading. Producer snapshots close
the later queue-to-drain window, not that earlier race. This feature promises
neither guaranteed pre-compaction durability nor recovery of arbitrary unlabelled
conversation prose.
