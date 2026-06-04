# Multi-device sync & data sovereignty — design (spec only)

Status: **local import preview slice implemented; no sync transport.** Engram is
local-first; this spec defines how an owner *could* keep one identity across
several machines **without** surrendering sovereignty. The shipped slice is
`import_engram(..., dry_run=True)` / `engram import <backup.json>`: a
metadata-only add/skip/conflict preview for owner-moved JSON backups. Nothing
here moves user data off-device, and live transport remains unimplemented until
the owner explicitly opts in and the privacy boundaries below are enforced.

## 1. Principle

The memory is the user's asset and lives on the user's machine. Multi-device
support must not become a silent cloud sync that turns local memory into someone
else's database. So this design starts from **local-first + owner-driven
transport** and treats any network path as opt-in, auditable, and revocable.

Non-negotiable invariants (any implementation must satisfy ALL):

```text
S1  Opt-in            — sync is off unless the owner explicitly enables it.
S2  Owner-held        — transport uses an owner-controlled channel (a file the
                        owner moves, or an owner-held remote they configure). No
                        Engram-operated server is in the default path.
S3  Local-first       — every device keeps a complete local store; sync reconciles
                        copies, it is not the source of truth.
S4  No content telemetry coupling — sync is a SEPARATE consent from telemetry.
                        Enabling telemetry never enables sync and vice-versa.
S5  Conflict-safe     — concurrent edits reconcile via the existing version chain
                        (supersedes / parent_id), never by silent overwrite.
S6  Auditable + revocable — every sync run appends a metadata-only audit record;
                        the owner can stop syncing and the local store is intact.
```

## 2. Sync model: export/import first, not live transport

The first (and possibly only) supported model is **owner-moved snapshots**,
reusing what already exists:

```text
Device A:  engram export-engram         -> a full local JSON backup (sensitive)
           (owner copies it to Device B by a channel THEY choose)
Device B:  engram import <file>              -> metadata-only merge preview
           engram import <file> --apply --yes -> owner-confirmed local merge
```

`export_engram` / `import_engram` exist and are owner-gated
(`maybe_refuse_owner_write` for import, export-owner gating for export). The
current implementation adds local **dry-run merge planning**, restores the v2
identity sections (`preferences`, `trust_boundaries`) from full backups, and
surfaces same-key divergent lessons/decisions as reviewable version-chain
candidates. It does not add a network layer. A live transport (e.g. an
owner-configured WebDAV/S3 bucket, or a git remote of the export) is a strictly
later, separately-gated increment — and even then it is the owner's remote,
satisfying S2.

## 3. Merge / conflict reconciliation

The implemented local preview uses today's existing merge keys:

```text
identity/project fields       -> field-level add/skip/conflict metadata
lessons                       -> dedupe by summary
decisions                     -> dedupe by question
playbooks                     -> dedupe by title
tools                         -> dedupe by tool name
```

The current dry-run preview reports add/skip/conflict counts and field-level
conflict metadata before the owner applies. Merge mode preserves existing
non-empty local identity/project fields and only fills missing values or merges
list/dict additions. Same-summary lessons and same-question decisions with
different semantic fields are flagged as `review_version_chain_candidate` with
ids and changed field names only; content values are withheld. The later
version-chain increment should reuse Phase 6's `version_chain` + typed
`supersedes` edges and Phase 8's `reconcile_proposal` so divergent candidates
can be materialized as reviewable versions and resolved by the owner rather
than silently clobbering a local edit (S5).

## 4. Privacy boundaries

```text
- Sensitivity gate applies on export scope: an owner may export a redacted
  subset (max_sensitivity) for a less-trusted device, reusing the governance
  ceiling. Default export is full + owner-only (treat as sensitive).
- secret-class items: never leave via a reduced-sensitivity export; only the
  full owner-only export carries them, and that file is labeled sensitive.
- No third party in the path (S2). Engram's telemetry endpoint is NOT a sync
  channel and must never receive store content (enforced by the metadata-only
  telemetry contract; see telemetry-validation).
- daily_id / telemetry identifiers are per-device and never used to correlate
  devices; sync identity is the owner's store, not a server-side account.
```

## 5. Rollout phases

1. Spec (this doc) + import **merge-semantics** design. (Done for local preview.)
2. Import-as-proposal: `import_engram(..., dry_run=True)` or
   `engram import <backup.json>` reports metadata-only add/skip/conflict counts
   before any Engram data write. (Implemented, local only.)
3. Conflict materialization via `supersedes` edges on explicit apply. (Future
   work; current apply remains conservative and non-destructive for conflicts.)
4. Optional owner-configured remote transport (owner's bucket/git), opt-in,
   audited — only after 1–3 are validated.

## 6. Non-goals

- No Engram-operated sync server (would break S2).
- No automatic background sync.
- No coupling to telemetry (separate consent — S4).
- No off-device move of user data in this pass (spec only).
