# Setup / Upgrade Safety — posture, checklist, and gaps (Task 12)

Status: **checklist/spec + characterization tests.** The existing safety
behaviors are now locked in by `tests/test_file_safety_invariants.py`. Net-new
items (rollback automation, cross-drive migration) are specified here as
bounded future work — not implemented in this pass.

## 1. What is already safe (verified)

| Property | Where | Test |
|----------|-------|------|
| Engram writes can't escape `ENGRAM_DIR` | `file_safety.write_engram_text` raises `PermissionError` outside root | `test_engram_write_refuses_outside_root` |
| External config writes require explicit authorization | `write_external_config_text(authorized=False)` raises; file not created | `test_external_write_refuses_without_authorization` |
| External deletes require authorization | `delete_external_config_file(authorized=False)` raises | `test_external_delete_refuses_without_authorization` |
| Existing files are backed up before overwrite | `backup_existing_file` → timestamped `.bak` under `<root>/backups/file_safety/<scope>/` | `test_authorized_external_write_backs_up_existing` |
| Unchanged content is a no-op | compare-then-write returns `None` | `test_engram_write_is_noop_when_unchanged` |
| Ledger is metadata-only + redacts external paths | `file_safety_ledger.jsonl` stores redacted label + `path_sha256_12` | `test_ledger_is_metadata_only_and_redacts_external_paths` |
| Setup reads external configs read-only by default | `setup` is read-only unless `--apply-external-config`; strict parse refuses to overwrite unparseable config | (manual; see CLI) |
| Dry-run exists | `engram feedback --dry-run`, `install_builtin_playbook(dry_run=True)` default | (manual) |
| Non-destructive JSON recovery | `engram recover-json <dataset>` analyzes backups, exports candidate, never overwrites live store | (manual) |

## 2. Pre-upgrade checklist (run before any version bump / re-setup)

```text
[ ] Back up the whole store:    copy ~/.engram (or $ENGRAM_DIR) aside.
[ ] Note ENGRAM_DIR:            echo $ENGRAM_DIR  (default ~/.engram)
[ ] Health check:               engram doctor
[ ] Redacted status snapshot:   engram status
[ ] Confirm external configs:   engram setup   (READ-ONLY; do NOT pass
                                --apply-external-config unless you intend writes)
[ ] If applying client config:  engram setup --apply-external-config
                                → backups are written automatically; verify a
                                  .bak appeared under <root>/backups/file_safety/
[ ] After upgrade:              engram doctor   (expect green)
[ ] If JSON looks wrong:        engram recover-json <dataset>   (dry-run first)
```

## 3. External config safety rules (already enforced — keep)

- Writing another tool's config is **opt-in** (`authorized=True` /
  `--apply-external-config`), never automatic.
- Every external write/delete is backed up first and recorded (metadata-only).
- A config that cannot be parsed is **not overwritten** — setup refuses and asks
  the user to fix or move it aside.

## 4. Gaps / net-new (bounded specs, NOT implemented here)

### 4.1 Rollback automation

Today: backups exist as sibling/area `.bak` files + a ledger, but restore is
manual. Proposed `engram rollback`:

```text
engram rollback --list                 # show recent file_safety ledger writes
engram rollback --last [--scope ...]   # restore the most recent backup(s)
engram rollback --to <ledger_ts>       # restore the backup taken at a point
```

Rules: dry-run by default (print what would be restored); require `--yes` to
apply; re-backup the current file before restoring (so rollback is itself
reversible); operate only on paths present in the ledger.

### 4.2 Cross-drive / ENGRAM_DIR migration

Today: `ENGRAM_DIR` is honored read-only; there is no migrate command, so moving
the store across drives is manual and easy to get wrong. Proposed
`engram migrate-dir`:

```text
engram migrate-dir --to <new_dir> [--dry-run] [--yes]
```

Rules:
```text
- dry-run first: report file count, total size, free space at destination,
  and any path that would collide.
- copy (not move) to destination; verify checksums; only then update the
  ENGRAM_DIR pointer guidance; never delete the source automatically.
- refuse if destination exists and is non-empty unless --force-merge.
- cross-drive: use copy+verify (atomic rename can't cross drives on Windows).
- record the migration in the file safety ledger.
- print explicit instructions to set ENGRAM_DIR; do not edit the user's shell
  profile automatically (external config = opt-in).
```

### 4.3 Test plan for the net-new work

```text
- rollback restores exact bytes; re-backs-up current first; dry-run writes nothing.
- migrate-dir dry-run reports correct counts and never writes.
- migrate-dir copy+verify; source untouched; collision refusal; cross-drive path.
- idempotency: re-running dry-run twice is stable.
```

## 5. Gates / do-not

- Cross-drive migration touches user data broadly → it is a **large task** under
  the operating protocol: needs tests + clear rollback + explicit user OK before
  implementation, per the Task 12 constraint.
- Never auto-edit the user's shell profile or another tool's config to point at a
  new ENGRAM_DIR — print instructions instead.
