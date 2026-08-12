# Embedded Host Facade — contract (phase 1)

For applications that embed piia-engram **in-process as a Python library** and
need to admit it by contract rather than by version string.

## Why it exists

An application that pins `piia_engram.__version__ == "4.14.0"` breaks on every
release, and the pin says nothing about whether the behaviour it depends on is
still there. The facade replaces that with a published contract: a capability
witness the host can verify, a handshake that fails closed, and a bounded read
whose result is hash-bound.

## Surface

```python
from piia_engram.embedded import (
    FACADE_CONTRACT_VERSION,      # "engram.embedded_host_facade.v1"
    SNAPSHOT_SCHEMA,              # "engram.embedded_task_context_snapshot.v1"
    capability_witness, verify_witness, write_capability_witness,
    handshake, require_compatible, FacadeHandshakeError,
    retrieve_task_context_snapshot, validate_snapshot, FacadeContextError,
    canonical_hash,
)

require_compatible(required_contract=FACADE_CONTRACT_VERSION)   # fail-closed
snapshot = retrieve_task_context_snapshot(
    engram_root=..., project_folder=..., project_id="proj-alpha",
    task_id="task-0007", task_class="software_development",
    objective="...", limit=8, previous_context_hash=None,
)
```

Only the names exported from `piia_engram.embedded` are covered by the contract.
Everything else in the package is implementation detail.

## Versioning rule

`FACADE_CONTRACT_VERSION` and `SNAPSHOT_SCHEMA` evolve **independently of the
product version**. A product release that does not change the facade surface
leaves both untouched, so a host pinned to a contract keeps matching across
upgrades. A breaking facade change bumps the contract version; hosts then get a
clean `facade_contract_mismatch` instead of silent drift.

## Capability witness

`capability_witness()` returns a content-free mapping: facade contract, snapshot
schema, runtime version, retrieval modes, the read-only guarantee, and a sha256
digest per facade source file — plus `witness_hash` computed over all of it.
`verify_witness()` re-checks the self-hash **and** re-hashes the live sources, so
a witness that no longer describes the loaded code is rejected.

Generate or verify from the CLI:

```
python scripts/generate_capability_witness.py -o witness.json
python scripts/generate_capability_witness.py --verify witness.json
```

## Handshake

`handshake(...)` compares the host's declared requirements against the facade and
returns `{match, problems, ...}`; `require_compatible(...)` raises
`FacadeHandshakeError` on mismatch. There is no partial match and no downgrade
path: unknown retrieval modes, a different contract version, or a different
snapshot schema all fail closed with a stable problem code.

## Bounded read: snapshot semantics

`retrieve_task_context_snapshot()` performs exactly one bounded read and returns
an immutable snapshot with these 18 envelope fields:

`schema`, `retrieval_query`, `scope`, `scope_hash`, `previous_context_hash`,
`source_hashes`, `included_count`, `matched_count`, `withheld_count`,
`provider_included_count`, `excluded_count`, `excluded_by_reason`, `items`,
`status`, `engram_read_only`, `engram_write_performed`, `provider_authority`,
`context_hash`.

Each item carries `source_kind`, `trust`, `applicability`, `sharing_class`,
`sharing_basis`, `public_equivalent_summary`, `counterevidence`,
`stop_conditions`, `source_hash`.

`context_hash` is a sha256 over every other field, canonicalised with
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`,
`allow_nan=False`. A host recomputes it to verify the snapshot end to end;
`canonical_hash` is exported so both sides hash identically.

- `tier` is always `verified`; `search_mode` is always
  `keyword_no_persistent_index` (the optional hybrid index is excluded because
  materialising it would write to the store and is not byte-stable across
  environments).
- `limit` is bounded to 1..8.
- `status` ladder: `context_available` →
  `local_context_available_provider_content_withheld` → `context_empty`.
  **`context_empty` is a legal, expected result**, not an error.
- `provider_authority` is always `False`. Trust and project scope grant no
  sharing authority: only a separately authored `public_equivalent_summary` is
  ever quoted; anything else is counted in `withheld_count` and never emitted.
- Errors are `FacadeContextError` with a stable opaque code
  (e.g. `context_limit_invalid`); message text never carries store content or
  filesystem paths.

### Project identity resolution

A host names a project with its own logical id. Engram scopes entries by a
**path-derived canonical id**, and it overwrites a caller-supplied `project_id`
on write. A consumer that compares a stored scope against its own namespace
therefore excludes every project-bound item as `project_scope_mismatch` —
silently, since exclusion is a normal outcome.

The facade resolves `project_folder` to Engram's canonical id (including legacy
aliases) and accepts either namespace, because it is the only layer that knows
this derivation. Scope safety is unchanged: an item still has to be bound to the
same project folder.

## Zero-write constitution

Phase 1 exposes **no write path**. The store is opened read-only, the persistent
index is never touched, and there is no network or subprocess use. This is
enforced by tests that (a) fingerprint every file under the store root before and
after a retrieval and (b) intercept write-mode `open`,
`os.replace/rename/remove/mkdir`, `shutil` copies and `Path.write_*` inside the
store root, failing if any fires. A guard-sanity test asserts the interceptor
catches a real write, so the proof cannot pass vacuously. Any capability that
requires a write is phase 2+.

## Not in phase 1

Writes of any kind; supersede/rollback primitives; a provider-safe projection
mode beyond the public-equivalent gate above; a published performance envelope;
a packaged contract-test kit for host CI.
