# Synthetic Dual-Client Continuity Matrix

This offline acceptance harness measures whether Engram returns the information
needed to continue a synthetic task after switching simulated clients. It uses
the real Engram stdio MCP server and real hook entry points. It does not launch
a coding client, call a model, contact a provider, or incur model cost.

Client X sends Claude Code-shaped hook payloads; Client Y sends Cursor-shaped
payloads. These are protocol simulators, not those applications. Both clients
also initialize independent MCP connections and call public tools over stdio.
Client names and tool provenance are self-reported, not authenticated identity.

## Run locally

Use an existing Python environment with Engram's runtime dependencies and Git
already installed. No installation or network access is part of the harness.
From the repository root, replace the two paths with an existing interpreter
and a new scratch directory:

```powershell
pwsh -NoProfile -File scripts/run_continuity_matrix.ps1 `
  -Python '/absolute/path/to/python' -WorkRoot '/absolute/path/to/new-run'
```

The launcher redirects the store, home, client config locations, caches and
temporary directories before starting Python. The scratch directory must not
already exist. It contains `results.json`, synthetic fixture repositories and
local observations. Keep generated observations local: they include scratch
paths. Use `-Scenario chinese_recall` to run one scenario. Exit code 1 means
there is an unmet acceptance contract; inspect the results rather than treating
an expected gap as a successful continuation.

The Python entry point is `scripts/continuity_matrix.py --work-dir NEW_DIRECTORY
--output RESULT.json`. Direct callers must establish the same isolated
environment before starting Python. The transport sends MCP JSON-RPC over
anonymous stdio pipes, including initialize, tools/list and tools/call; no
in-process memory API stands in for an MCP roundtrip.

## Preregistered contracts

The fixture manifest is
[`continuity_matrix_cases.json`](../../tests/fixtures/continuity_matrix_cases.json).
Its nine scenarios, two directions, two integration modes and two approval modes
produce 72 independently isolated rows. Contracts are fixed before execution.

| Dimension | Values |
|---|---|
| Direction | Client X to Client Y; Client Y to Client X |
| Integration | Manual MCP with explicit drain; automatic hook capture and injection |
| Approval | Public default; strict |
| Scenarios | Cold start; restart and compaction; linked Git worktrees; same-name different projects; duplicate events; held store lock and recovery; unwritable destination and recovery; source attribution; Chinese recall |

Each task preregisters goal, constraint, blocker, last failure reason, next
action and source. Every field in these fixtures exists and is legal task
evidence. The checker also supports a null field for a future case in which
that field must remain unknown; null fields are excluded from the denominator.
The source field is resolved to the sending adapter's tool label and requires
Engram's native source annotation and warning that earlier session records are
unreviewed and grant no approval or action authority. A user-written source
line alone does not satisfy it.

The receiver gets `get_resume_brief` and `get_recall` at a fixed 1500-token soft
budget. The checker matches the preregistered values literally in delivered
content, recording per-surface matches and the combined field result. A pointer,
request argument, write acknowledgement or source fixture does not count as
recovered task content. No model judges paraphrases or fills in missing fields.

Every row includes a reachable current knowledge control, a staged control, a
superseded control and an unrelated project control. A real MCP write also tests
default versus strict admission: synthetic `user_confirmed=true` does not
bypass strict staging. This does not approve any live memory. The same-name
negative control reads both projects and checks that task fields do not cross
their boundary. Linked worktrees are created only inside fixture repositories.

## Read the results by mode

`direct` records the receiving MCP output before any successful explicit drain.
`after_drain` records recovery after the offline processor; hook rows also record
the real SessionStart injection output. `recoverable_after_drain` and `accepted`
are separate. A hook row cannot pass automatic end-to-end acceptance merely
because a manual processor run makes it recoverable. Manual mode includes its
explicit drain, including when there were no queued events.

Current Stop hooks enqueue capture events; ordinary MCP reads and SessionStart
injection do not process that queue. Consequently, newly queued task content
does not establish automatic end-to-end continuity. Cursor-shaped summaries
can carry task fields through the explicit processor. Claude Code Stop capture
records activity metadata rather than the full task body; those six-field
acceptance gaps remain visible, with strict xfail regression witnesses. The
harness does not repair these behaviours or infer a successful host handoff.

Held-lock trials use a real checkpoint write lock. The portable unwritable trial
places a file where a checkpoint directory must be, observes rejection or
deferred processing, removes that fixture barrier, and retries. It is not an
operating-system ACL or full-disk test. Duplicate hook trials replay the same
event bytes after receipt creation; manual checkpoint calls retain their native
append semantics and check one logical session rather than claiming request
idempotency. Compaction uses the supported PreCompact flush threshold and
PostCompact archival entry for Client X and sessionEnd for Client Y.

The JSON records every step's elapsed milliseconds, total elapsed time, queue
outcomes, Owner review item count, zero model calls and zero model cost. Review
counts include the deliberately staged control plus any pending synthetic
write. Latency is a local observation, including process startup, not a stable
performance promise. UUIDs, timestamps and timings vary; field checks and case
selection are deterministic.

`wrong_adoption` counts forbidden-body matches across receiving outputs:
cross-project, pending/staged or superseded knowledge must not appear as
current approved information. The fixture checks conservatively reject these
bodies entirely in the default views. A complete continuation still fails
acceptance if this count is nonzero. This is an output contamination proxy, not
an observation of what a model actually adopts. Raw checkpoints remain earlier
session evidence, including in strict mode.

## Evidence boundary

This proves only the observed synthetic capture, persistence, filtering,
recovery and output contracts at the tested source revision. Separate results
for modes, directions and approval settings must stay separate. It does not
prove real host hook delivery, client configuration, model understanding,
correct task execution, behaviour improvement, arbitrary transcripts, other
budgets, encryption coverage, ACL recovery or real-world reliability.

No real client receives a new verification level from this matrix. Unverified
clients remain unverified. The case manifest and transport-independent checker
can be reused in a later real-host comparison; that comparison still needs its
own controls, host observations, model settings and cost accounting. See
the [client evidence index](client-continuity-evidence.md) for existing evidence
and its claim boundaries.

## 中文说明

本矩阵验证的是两个模拟客户端经真实 MCP stdio 和真实 hook 入口传递合成任务信息。
手动 MCP、自动捕获、显式 drain 后恢复分别计分；排队成功不代表自动接续成功。
目标、约束、阻塞、失败原因、下一步与来源逐字段匹配，待审、历史替代版本和跨项目
内容不得混入当前可信输出。模型调用与费用始终为零，审核数量另计。

它不验证真实宿主、模型理解或任务收益，也不提升任何真实客户端的已验证级别。
未验证的客户端仍然未验证。Claude Code Stop 格式的任务正文缺口保留为严格 xfail
见证；本次仅呈现结果，不修改产品捕获行为。
