# Resume budgets and capture diagnostics

[English](resume-capture.md) | [中文](resume-capture.zh-CN.md)

`get_resume_brief` selects current state, the next action, a blocker or latest failure,
key constraints, and source/review/freshness information before background and history.
The optional `project_resume_pack.v1` and `agent_context_pack.v1` use the same selected
handoff. Their switches still default to off. Direct Python pack builders accept the
same optional `token_budget` (default 2000); there is no new MCP tool.

| Budget | Contract for ordinary short fields |
| --- | --- |
| 128 | Keep the next action and blocker/failure, with source status; bound each key independently. Report any lost detail. |
| 256 | Keep the next action, recent blocker/failure and key constraint. Background can be omitted. |
| 512 / 1500 | Recover the ordinary sample's key fields; long fields still carry explicit cuts. |
| Default (2000) | Apply the same priority, with room for supporting context. |

`estimated_tokens` is a soft estimate of rendered Markdown (roughly four characters
per token), not a tokenizer measurement or hard wire-size cap. Wrappers, headings,
omission notices and the presence line have costs; `budget.over_budget` says when the
estimate exceeds the effective requested budget. A small estimate may be exceeded to
keep the only action or blocker visible. Structured packs and response metadata add
wire size; their serialized JSON is not included in `estimated_tokens`. The contract
does not promise that arbitrary-length fields fit in 128 tokens. Budgets below 128
retain the identity fallback and report the absent handoff.

The Claude Code and Cursor SessionStart hooks inject this brief, and that read has a
time budget of 10 seconds by default, covering the Engram import and the store read.
`ENGRAM_RESUME_HOOK_BUDGET_S` (seconds) overrides it: an invalid value uses the
default, a value below 1 second is raised to 1, and the result is capped one second
below the timeout the hook is registered with (14 seconds for Claude Code's 15-second
SessionStart registration). When the budget runs out, the session starts without the
brief; `engram doctor` reports how often that happened in the last 7 days.

Unknown or conflicting source fields remain unknown or follow the existing checkpoint
arbitration. Engram does not invent a missing next step. `omitted` names cut sections
and fields, carries the existing budget reason/count/eligible IDs, and adds a
`retrieval_hint`. The packs reuse their existing omitted metadata and retrieval hints.
Use a larger brief budget, `get_project_snapshot` (including `checkpoint_history`),
`get_recent_context`, `get_session_digest`, or the eligible knowledge search/history
paths. A pointer or cut prefix is not the recovered full field. Retrieval remains
subject to the same scope and permission rules.

Raw checkpoints and summaries are labelled **earlier session record**: unreviewed
reference material with no approval or action authority. Snapshot/constraint entries
in the existing context list carry that source status, even though the list is named
`trusted_context` for compatibility. Pending knowledge stays a candidate; eligible
knowledge keeps its existing tier and review status. `fresh` describes freshness,
`validated` describes validation, and `verified` remains the knowledge tier; none
alone creates current user authorization. Strict-mode eligibility, approval rights,
sensitivity limits, storage schemas and owner-only trust details are unchanged.

The brief and capture diagnostics show a store identity. The display uses doctor's
existing path-shortening convention: a home-relative name, or only the leaf name
outside home. A stable local hash of the normalized root distinguishes stores with
the same name. Compare the identity when different clients appear to resume different
work; it identifies a local root, not an account or shared-store permission.

`engram doctor` and `engram doctor --json` report capture metadata:

- pending count/bytes and oldest age;
- protected/legacy pending counts and retained failure status count/bytes;
- quarantine count and bytes (including reason sidecars), partial count/bytes across
  the spool, quarantine and receipts directories, and receipt count/bytes;
- up to 20 recent result codes from existing event envelopes, quarantine reasons and
  receipts: `processed`, `duplicate`, `invalid-event`, `invalid-prepared-event`,
  `pending-cap`, `transcript-missing`, `transcript-missing-age-limit`,
  `transcript-missing-retry-limit`, `storage-unavailable`, `processing-failed`,
  `receipt-failed`, `transport-unavailable`, `unknown`, and the closed crypto
  failure codes in the [queue protection guide](hook-queue-protection.md);
- read-only cleanup candidates older than seven days, summarized as counts and bytes;
- whether automatic processing is on, and the last automatic pass: when, by which
  process (`mcp-server` or `watcher`), and how many events it processed, failed and left.

These diagnostics never echo summaries, transcript locations, exception text, event
IDs or bodies. Older envelopes without result metadata have no reported result;
unrecognized or unreadable result metadata is `unknown`. Outcomes are local evidence,
not confirmation of host consumption. Metadata collection can report
`metadata-unavailable` when local files cannot be read. Counts are a read-only
snapshot and may change during concurrent capture.

**Queued** means locally queued, not confirmed durable knowledge. A **processed**
receipt means local processing finished; the event can produce a session record or
staging knowledge, and does not mean approved. Hook output success does not confirm
the host adopted context: host consumption is **unknown** without confirmation.

A backlog of at least 100 events or an oldest event at least one day old shows the
existing local command hint. Verify the displayed store first, then preview and drain:

```bash
engram hooks drain --dry-run --json
engram hooks drain --json
```

The queue is processed automatically by two processes that are already running on
your machine:

- the Engram MCP server, from a background thread: a first pass 15 seconds after it
  starts (`ENGRAM_SPOOL_AUTODRAIN_DELAY_S` changes that), then one every ten minutes,
  sooner while a backlog is being worked off;
- the watcher (`engram watcher`), after each scan, at most once a minute.

One automatic pass handles at most 50 events or runs for 20 seconds, oldest first, so
a large backlog is cleared over several passes and never holds the store. It writes
what `engram hooks drain` writes: session records, digests and pending proposals in
the review queue. It is never recorded as an AI client's write, and it approves
nothing: every proposal still waits for your `engram review`, in every approval mode.
A read-only server starts no queue thread. When the queue key of a protected queue is
not available to the server, it stops trying for that process and leaves the events
for a process that has the key. After a pass in which every event failed, the next
one waits longer (doubling, up to two hours).

Set `ENGRAM_SPOOL_AUTODRAIN=0` to turn automatic processing off: in the `env` of the
Engram MCP entry for the server, and in the watcher's environment for the watcher.
Then only `engram hooks drain` processes the queue. `engram doctor` shows whether it
is on in the environment doctor runs in and what the last automatic pass did (time,
which process, counts).

Doctor, ordinary reads and MCP tool calls themselves do not drain. A diagnostic or
dry-run does not create an absent store, acquire its queue lock, or consume events.

Retention keeps the existing pending cap (1,000 events / 16 MiB; 128 KiB per event).
This is **not a total disk-use cap**: quarantine, partial evidence and receipts can
use additional space. Concurrent maintenance can temporarily exceed the pending cap.
Quarantine/partial cleanup candidates are a report for local inspection, not permission
to delete; preserve recovery evidence and any active write before manual handling.
Nothing is automatically deleted by diagnostics. Receipts are kept by default to
preserve deduplication, and never become cleanup candidates in this policy.

When store encryption is enabled on the intended root, new queue bodies and their queue publication
intermediates are protected. Legacy plaintext and downstream archives, digests,
quick-context, project snapshots and some knowledge metadata remain outside this
boundary. See [hook queue body protection](hook-queue-protection.md) for scope,
failure recovery, byte limits and version compatibility. Host adoption still
requires separate confirmation.

This policy is root-local opt-in. Every writer must use that root; copies must
preserve the policy and original salt, and each host needs the secret. Losing both
local signals in an incomplete restore can select plaintext when the secret is
unset. Quiesce writers during enablement or rely on the shared policy gate used
by current writers and enablers; earlier plaintext remains legacy exposure.
