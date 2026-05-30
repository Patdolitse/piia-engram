# Agent Governance

Engram is local-first memory for AI tools. The governance layer is an
optional runtime boundary for deciding what a calling agent may read, which
write-like operations it may perform, and what disclosures were made.

Governance is **off by default**. Enable it only when you want per-caller
filtering and audit receipts:

```bash
export ENGRAM_GOVERNANCE=1
export ENGRAM_CLIENT_TYPE=claude_code
```

On Windows Command Prompt:

```bat
set ENGRAM_GOVERNANCE=1
set ENGRAM_CLIENT_TYPE=claude_code
```

When governance is off, the `maybe_*` governance helpers are designed to be
true no-ops: callers receive the same payloads and ordinary Engram behavior is
unchanged.

## Trust Levels

The MCP caller identity is currently self-reported through
`ENGRAM_CLIENT_TYPE`. Known client names are mapped to one of three trust
levels. Unknown or empty client types fail closed to the most restrictive
level.

| Trust level | Typical client types | Read ceiling | Write policy |
|---|---|---|---|
| `private-self` | `self`, `cli`, `engram`, `doctor` | `secret` | `verified` |
| `trusted-local` | `claude_code`, `claude-code`, `codex`, `cursor`, `windsurf`, `gemini_cli`, `gemini-cli` | `work` | `proposed_only` |
| `read-only-external` | unknown, empty, web or transient callers | `public` | `no` |

Important current-state note: `proposed_only` is a policy label today, not a
full staging workflow for direct MCP writes. Ordinary knowledge-store writes
from `trusted-local` are still allowed by `maybe_refuse_write()`. High-blast
operations such as grant changes, whole-store imports, and file exports are
stricter and require `private-self`.

Explicit grants can override the default mapping:

```bash
engram grants
engram trust <agent_id> <trusted-local|read-only-external|private-self>
engram revoke <agent_id>
```

Revocation is forward-only. It stops future disclosure; it cannot recall
context that has already been returned to a model.

## Sensitivity Model

Knowledge items are compared against the caller's trust ceiling using this
ordered sensitivity ladder:

```text
public < work < private < secret
```

Unlabeled items default to `work`, not `public`. Present but malformed labels
fail closed to `secret` in the live read path. This prevents an invalid
`sensitivity` value from silently downgrading an item into a lower tier.

The read gate returns original item objects minus excluded items. It should not
inject sensitivity fields or reshape allowed items. For single-item or opaque
responses that cannot be filtered safely, lower-trust callers receive a
withheld stub or a refusal string.

## Runtime Gates

The MCP layer routes tools through a deny-by-default governance matrix in
`src/piia_engram/mcp_server.py`:

| Tool class | Gate | Effect when governance is enabled |
|---|---|---|
| `read` | `maybe_govern_list`, `maybe_govern_result`, `maybe_govern_one`, or `maybe_govern_owner_only` where needed | Filters item lists/results by sensitivity, or withholds opaque aggregate views from non-owners. |
| `governed_write` | `maybe_refuse_write` before mutation | Refuses `read-only-external` and revoked callers. Allows `private-self` and, currently, `trusted-local`. |
| `owner_only_write` | `maybe_refuse_owner_write` before mutation | Allows only unrevoked `private-self`. Used for grant-store changes and whole-store imports. |
| `export_owner_only` | `maybe_refuse_export` before file creation | Allows only unrevoked `private-self`; non-owners get a refusal and no export/report file is written. |
| `safe_allowlist` | Explicit review only | Reserved for reviewed exceptions that do not need a write gate. |

The classification is enforced by tests. A new `@mcp.tool` that is not in
`TOOL_GOVERNANCE_CLASS` should fail the write-gate matrix tests.

## File-Side-Effect Controls

Several read-like tools can write files as a side effect: exports, review
pages, quick context, identity cards, and playbook execution plans. These are
classed as `export_owner_only`, so the owner check runs **before** the writer.
For non-owners, the file is not created.

The read-path matrix also covers derived side effects such as hybrid search
index rebuilds. With `ENGRAM_GOVERNANCE=1`, non-owner search callers do not
write a full-body hybrid index before their returned results are filtered.

## Disclosure Ledger

Governed reads and refusals write disclosure receipts to:

```text
~/.engram/governance_ledger.jsonl
```

or to the root selected by `ENGRAM_DIR`.

The ledger is append-only JSONL with a SHA-256 hash chain over each record's
sequence, timestamp, previous hash, and event payload. Verify it with:

```bash
engram verify-ledger
```

If the ledger tail is corrupt, append refuses rather than extending a broken
chain. Read filtering itself remains the hard guarantee: a failed ledger append
must not cause a secret item to be returned.

This ledger is separate from the optional operational access log controlled by
`ENGRAM_AUDIT=1`, which writes `~/.engram/audit.log`. The audit log can contain
operation details such as write summaries, so `get_audit_log` is treated as an
owner-only aggregate surface under governance.

## Security Boundaries

Governance is a local-first policy boundary, not a hardened sandbox.

- MCP stdio caller identity is self-reported through `ENGRAM_CLIENT_TYPE`.
- A hostile local process with filesystem access can still read files directly.
- Revocation only affects future Engram disclosures.
- The ledger detects ordinary tampering, corruption, overwrite, or reordering;
  it is not a defense against a malicious local administrator.
- Use OS file permissions, disk encryption, and `ENGRAM_SECRET` for stronger
  local data protection.

## Encryption And Search Interaction

`ENGRAM_SECRET` encrypts corpus fields with `enc:v2c:` payloads when the secure
extra is installed. When encrypted corpus data exists, Engram fails closed if
the matching `.corpus_salt` is missing.

Hybrid search is opt-in via `ENGRAM_SEARCH=hybrid`. Persistent search indexes
are rebuildable derived data, not the source of truth. When corpus encryption
is enabled, the code refuses persistent index paths that would copy encrypted
corpus content into plaintext search artifacts.

See also:

- [Hybrid Search](hybrid-search.md)
- [Architecture](architecture.md)

## Practical Setup Patterns

For a normal local coding-agent session:

```bash
export ENGRAM_GOVERNANCE=1
export ENGRAM_CLIENT_TYPE=claude_code
```

For owner maintenance in a terminal:

```bash
export ENGRAM_GOVERNANCE=1
export ENGRAM_CLIENT_TYPE=cli
engram grants
engram audit
engram verify-ledger
```

For a web, remote, or untrusted caller, do not set a known local client type.
Unknown callers resolve to `read-only-external`.
