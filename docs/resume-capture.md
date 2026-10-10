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
- quarantine count and bytes (including reason sidecars), partial count/bytes across
  the spool, quarantine and receipts directories, and receipt count/bytes;
- up to 20 recent result codes from existing event envelopes, quarantine reasons and
  receipts: `processed`, `duplicate`, `invalid-event`, `invalid-prepared-event`,
  `pending-cap`, `transcript-missing`, `transcript-missing-age-limit`,
  `transcript-missing-retry-limit`, `storage-unavailable`, `processing-failed`,
  `receipt-failed`, `transport-unavailable`, or `unknown`;
- read-only cleanup candidates older than seven days, summarized as counts and bytes.

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

Doctor, ordinary reads, MCP startup and MCP-origin extraction do not auto-drain.
There is no background service. A diagnostic or dry-run does not create an absent
store, acquire its queue lock, or consume events.

Retention keeps the existing pending cap (1,000 events / 16 MiB; 128 KiB per event).
This is **not a total disk-use cap**: quarantine, partial evidence and receipts can
use additional space. Concurrent maintenance can temporarily exceed the pending cap.
Quarantine/partial cleanup candidates are a report for local inspection, not permission
to delete; preserve recovery evidence and any active write before manual handling.
Nothing is automatically deleted by diagnostics. Receipts are kept by default to
preserve deduplication, and never become cleanup candidates in this policy.

Spool body encryption and real host adoption trials are separate work. This change
does not extend corpus encryption to spool bodies, or claim an end-to-end task benefit.
