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
| Metadata-only local backup plan | `recovery.build_backup_plan` / `engram backup-plan` enumerates Engram-owned files only, reads no knowledge bodies, modifies nothing | `tests/test_backup_plan.py` |
| Engram ops never touch external project files | backup-plan + recovery analysis snapshot-verified against an external project dir | `test_backup_plan_does_not_touch_external_project` |
| Symlink escaping the root is excluded from the plan | each path re-checked with `classify_path`; out-of-root paths excluded + counted | `test_symlink_outside_root_is_excluded` |

## 2. Pre-upgrade checklist (run before any version bump / re-setup)

```text
[ ] Review what to back up:     engram backup-plan   (metadata-only; no bodies)
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

## 3.5 Local backup plan + data sovereignty (implemented)

`recovery.build_backup_plan(root)` (CLI: `engram backup-plan [--json]`) answers
"what should I copy before upgrading?" with a **metadata-only** plan:

- It enumerates only Engram-owned files under the active root, grouped by
  top-level directory, plus per-dataset entry counts + sizes + sha256 prefixes
  for the precious knowledge files (`lessons`, `decisions`).
- It reads **no** stored knowledge bodies (summaries/choices/details never appear
  in the plan — `test_plan_is_metadata_only_no_bodies`).
- It modifies nothing (`live_store_modified: false`) and never reaches outside
  the Engram root: every candidate path is re-checked with
  `file_safety.classify_path`, and anything resolving outside the root (e.g. a
  symlink) is **excluded and counted** in `external_paths_excluded`, with
  `external_files_included` held at `0` as an enforced invariant.

**Data sovereignty boundary (local-only):** Engram backs up and restores *only*
its own root directory. It never copies, modifies, or deletes files in the
user's project folders. The restore procedure is deliberately manual and
explicit: stop MCP clients, copy the saved root back in full. This keeps the
"the user owns their data, locally" promise honest — there is no remote backup
and no cross-tree write.

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
