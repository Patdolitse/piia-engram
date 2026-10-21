# Hook queue body protection

When store encryption is enabled with `ENGRAM_SECRET` and the optional
`cryptography` dependency, new hook queue bodies use the store's existing corpus
key and `enc:v2c:` AES-256-GCM format. Initialize the encrypted store normally
before capturing hooks: producers only read its existing 16-byte `.corpus_salt`;
they never initialize a store, create a salt, repair keys or acquire its knowledge
lock. Keep the original secret **and salt** in your recovery plan.

Encryption is a **root-local opt-in** policy. Without a secret, salt file or required marker, hooks
keep their existing plaintext queue behavior. A nonblank secret, any salt-file
presence, or `hooks/spool-protection.json` in required mode means protection is
required. Writable encryption enablement and the first protected publication
establish that small marker. It is sticky: removing the environment variable
does not permit a plaintext downgrade. An unknown or unreadable policy refuses
capture. A missing or corrupt salt is never regenerated while that marker or
queue ciphertext remains. Restore the original secret/salt; do not replace the
salt to try to recover encrypted events.

Every writer must use the intended store root. A second root or host without a
propagated secret or local policy/salt signals selects plaintext; a writer missing
only its secret against the same initialized root refuses safely. An incomplete
backup/restore or a copy of selected directories can lose **both** marker and salt
and select plaintext when the secret is unset, even if queue ciphertext survives.
Copies must preserve the policy and original salt, and each host needs the original
secret. Configuring a secret before initialization refuses capture without a salt,
but that failed attempt creates no durable intent: losing the environment afterward
leaves an uninitialized root with no local signal.

Quiesce writers during enablement, or use writers and enablers that all honor the
same root-local `hooks/.spool-policy.lock` gate on a filesystem supporting their
shared file locks. Plaintext writers hold this gate shared, so they do not block each
other; a plaintext writer waits at most 0.25 seconds of its remaining capture
budget for an enablement in progress, rechecks all signals while holding it, and keeps it
through partial publication and replace. A gate that stays busy or a new signal
requires resealing within the remaining capture budget; without usable keys,
capture is refused. Writable enablement waits at most one
second for the gate and commits the required marker before creating/replacing the
salt while holding it. Prepared legacy replacements follow the same publication
rule; failure preserves the original bytes and records a closed reason. This gate
does not coordinate separate roots/hosts with independent files or older writers.
Plaintext published before enablement completed remains disclosed legacy exposure
and is sealed on ordinary drain; enablement is not a bulk rewrite.

## Protected surface and limits

Schema 2 encrypts every payload and prepared field together, including summaries,
contexts, paths, roots, session identifiers, free-form event names, flags and
checkpoint provenance. Initial events, prepared replacements, missing-transcript
retries and all their queue `.partial` files pass through the same encoder before
any event file is opened. Quarantine moves the original bytes without decoding.

The clear envelope contains only schema, canonical event ID, fixed kind/client,
creation time, protection format, salt fingerprint, bounded missing-transcript
counter and ciphertext. A domain and duplicate immutable header inside the
authenticated body are compared before receipt lookup or any processing effect.
Header/body substitution is refused. This does not authenticate the retry counter,
prevent rollback of a valid event, or authenticate legacy plaintext events.

The existing cipher and KDF are unchanged: AES-256-GCM, random 12-byte nonce and
PBKDF2-SHA256 with 600,000 iterations. The 128 KiB event limit covers the **final
UTF-8 JSONL bytes, including encryption overhead and newline**, on every write
and read. Usable body size is roughly 96 KiB minus envelope/binding overhead.
Oversize prepared input retains the previous event; it is not silently truncated
or processed without its frozen fields. Pending caps remain 1,000 events and
16 MiB of actual event bytes. Quarantine, partials, status records and receipts
are outside that pending-only cap.

Input, intent discovery (policy/salt/secret checks), and both plaintext and encrypted
encoding share a 1.0-second application deadline. Policy checks, crypto readiness,
sealing and transition resealing use at most 0.5 seconds of the remaining budget,
with a 25 ms diagnostic reserve. Expired results cannot start event publication.
Each new hook process derives
its own memory-only key. Slow machines fail closed; an abandoned crypto worker
cannot publish later. Interpreter/package startup and final filesystem
publication/fsync/replace retries add time beyond that application budget.

## Recovery and diagnostics

Missing/invalid prerequisites refuse enqueue, leave debounce unmarked and keep
the host hook's fail-soft exit behavior. A queued ID confirms publication only.
On drain, wrong keys, damaged ciphertext, changed salts or invalid bindings keep
the original pending bytes, without a processing receipt, pending unlink,
transcript retry increment or store effect. Other healthy events can proceed.
Restore the original secret/salt and retry the ordinary local drain.

Failure status files (`<event filename>.status.json`) contain only a version,
closed reason code and UTC timestamp. They follow overflow events into quarantine
and remain as evidence after successful retries. New hook diagnostics and audit
records contain no candidate body; Cursor debug output uses fixed shape/count
fields, and new debounce keys are hashed. This does not erase existing logs.

| Code | Local action |
| --- | --- |
| `crypto-secret-missing` | Supply the original secret to this process. |
| `crypto-salt-missing`, `crypto-salt-invalid` | Restore the original regular, non-symlink 16-byte salt. |
| `crypto-unavailable` | Restore the existing optional dependency/key environment; retry locally. |
| `crypto-timeout` | Retry on a machine able to derive the unchanged key within the budget. |
| `crypto-decrypt-failed` | Restore the original key or an intact event backup; no success is assumed. |
| `crypto-salt-mismatch` | Restore the original salt; the fingerprint mismatch does not identify its cause. |
| `protection-unavailable` | Restore policy/gate access or retry after a busy enablement gate; no automatic downgrade occurs. |
| `event-oversize` | Inspect the capture size; the prior event is retained on preparation failure. |

`engram doctor --json` and `engram hooks drain --dry-run --json` inspect only
bounded metadata, with protected/legacy pending counts and status usage/codes.
They need no key, decryption, KDF, lock or policy creation, preserve unrelated
diagnostics when encrypted-store checks fail, and never migrate or consume work.
Actual drain retains the existing staging gates, per-operation deduplication,
archive markers and checkpoint provenance. A durable processing receipt means
neither Owner approval nor confirmed host adoption. No new automatic drain,
network path or background service is introduced.

## Compatibility and remaining plaintext

Schema 1 remains readable. Existing plaintext events, partials, quarantine and
logs are **legacy exposure** and are not rewritten by startup, doctor or dry-run.
During ordinary authorized drain in required mode, successfully prepared legacy
events are sealed as schema 2 before store effects, preserving IDs and ordering.
Key/sealing failures retain the legacy source bytes. In plaintext mode they
continue to process normally. Older Engram versions cannot drain schema 2 queues;
do not run an older drainer against them. There is no bulk migration command in
this change; backup/restore migration needs a separate design including original
salts, partials and quarantine. No secure-erasure guarantee is made.

This is **queue body protection**, not whole-store or end-to-end encryption.
The following downstream/source outputs remain plaintext:

- session Markdown and daily compact-summary Markdown, including their archive
  publication `.partial` files outside the spool;
- session digest JSON and its temporary/backup copies;
- `quick_context.md`, its publication intermediates and project snapshots;
- metadata and non-digest `extraction.evidence_span` in knowledge rows (existing
  field encryption does not encrypt the entire row);
- original client transcripts, previous logs, existing backups and other files
  outside the queue boundary.

Protecting those outputs requires separate compatibility work or an appropriate
filesystem/storage policy. Back up and inspect them with that scope in mind.
