# Context Governance

Context governance previews help an AI client reason about what context is safe
to show, refresh, replay, or turn into local evidence. They are local proposal
surfaces. They do not publish, push, tag, release, upload, archive, merge, or
apply changes.

## The MCP surface

`preview_context_governance` is an advanced MCP tool. It is not loaded in the
default Tier-1 core set; set `ENGRAM_TOOLS=all` to expose it.

When governance is enabled, this tool is owner-gated before store-backed modes
can read local memory. The tool returns JSON with:

- `mode`: the selected preview mode
- `proposal`: the local draft or proposal
- `applied: false`
- `invariant: context_governance_preview_only`

## Modes

| Mode | Input | Output | Boundary |
|---|---|---|---|
| `safe_context` | A supplied context payload, or current recall if no payload is supplied | Redacted and budget-trimmed context | Does not decide who may read it; governance still applies before store-backed recall. |
| `freshness_conflicts` | Local lessons and decisions | Metadata-only review proposals | Returns ids, counts, action names, and reason codes; does not return stored bodies and does not archive anything. |
| `replay_packet` | A compact summary | Redacted replay packet | The packet is not written or applied; it is a local handoff draft. |
| `external_evidence` | Evidence rows supplied by the caller | Markdown local draft | Requires owner confirmation before any public use. |

## What It Does Not Do

- It does not mutate stored knowledge.
- It does not promote staging knowledge.
- It does not archive stale knowledge.
- It does not resolve conflicts.
- It does not apply replay packets.
- It does not write files.
- It does not publish external evidence pages.
- It does not approve GitHub, PyPI, MCP Registry, or release actions.

## Example Inputs

Safe context from a supplied payload:

```json
{
  "mode": "safe_context",
  "payload_json": "{\"knowledge\":[{\"summary\":\"api key sk-test_1234567890abcdef1234567890abcdef\"}]}",
  "options_json": "{\"max_chars\":2000}"
}
```

Freshness and conflict proposal from the local store:

```json
{
  "mode": "freshness_conflicts"
}
```

Replay packet:

```json
{
  "mode": "replay_packet",
  "payload_json": "{\"compact_summary\":\"Continue from the previous review.\"}",
  "options_json": "{\"source\":\"postcompact\",\"max_summary_chars\":1200}"
}
```

External evidence draft:

```json
{
  "mode": "external_evidence",
  "payload_json": "{\"evidence\":[{\"label\":\"PyPI\",\"status\":\"verified\",\"checked_at\":\"2026-06-06\",\"url\":\"https://example.test\"}]}",
  "options_json": "{\"title\":\"Release Evidence\"}"
}
```

## Publication Guard

The `external_evidence` mode deliberately renders a local draft with a
publication warning. A draft is not approval. Public actions still require the
owner to confirm the exact operation later.
