# Runbook: Install / data-sovereignty audit

Status: **local-first boundary, test-backed.** This documents the data
sovereignty guarantees and how to verify them. The boundary is: your identity
and knowledge live on your machine, under one root you control, and nothing
leaves it silently.

## 1. The active root

Engram resolves its root in this order:

1. `ENGRAM_DIR` environment variable (highest priority);
2. `~/.engram` (modern default);
3. `~/.piia` (legacy) — used only if `~/.engram` does not exist.

Verified by `tests/test_storage.py` and
`tests/test_data_sovereignty.py::test_engram_root_prefers_env_then_legacy`.

## 2. Everything protective stays under the root

| Artifact | Location | Guarantee |
| --- | --- | --- |
| Knowledge / identity | `<root>/...` | Engram writes refuse to escape the root (`PermissionError`) |
| Backups | `<root>/backups/...` | Even a backup of an **external** file is stored under the root |
| File-safety ledger | `<root>/file_safety_ledger.jsonl` | Metadata-only; external paths redacted to `<external:hash>` |
| Governance ledger | `<root>/governance_ledger.jsonl` | Append-only, hash-chained, local |

Verified by `tests/test_data_sovereignty.py`
(`test_ledger_lives_under_root`, `test_backup_of_external_file_stays_under_root`)
and `tests/test_file_safety_invariants.py`.

## 3. No silent external writes

External client configs (Claude Code/Desktop, Cursor, Codex, …) are **read-only
by default** during setup. A write happens only when you explicitly run
`engram setup --apply-external-config`, and even then the existing file is backed
up first and the action is recorded (metadata-only) in the ledger.

- An unauthorized external write raises and leaves **no file and no ledger
  entry** — verified by
  `tests/test_data_sovereignty.py::test_refused_external_write_leaves_no_file_and_no_ledger`.
- The ledger never stores raw external paths or file bodies — only a redacted
  label, a hash, and a timestamp.

## 4. No silent cloud / network path for identity or knowledge

Identity, lessons, decisions, playbooks, and contexts are stored as local files
under the root. There is no implicit upload of this data. Optional telemetry is a
separate, schema-validated, content-free contract (see
`docs/telemetry-privacy.md` and the telemetry-contract runbooks); it carries no
knowledge bodies.

## 5. Reproduce the audit

```bash
python -m pytest tests/test_storage.py tests/test_file_safety_invariants.py \
                 tests/test_data_sovereignty.py -q

# Metadata-only local backup plan (reads only counts/sizes/hashes, never bodies):
engram backup-plan
engram backup-plan --json
```
