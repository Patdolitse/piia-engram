# Runbook: Encoding / mojibake data scan (safe, dry-run-first)

Status: **dry-run-first, non-destructive.** This is the documented path to check
real Engram data for historical mojibake (UTF-8 text previously decoded with the
wrong codec, e.g. GBK/Latin-1) without any risk of damaging stored knowledge.
There is no separate scan logic — it reuses `piia_engram.encoding_repair`.

## 1. Dry-run scan (default — never writes)

```bash
engram repair-encoding            # owner view: lists findings (path:field, reason)
engram repair-encoding --summary  # metadata-only: counts + reason codes, no bodies/paths
```

- The default dry-run prints up to 20 findings as `path:field (reason)` for the
  owner to inspect locally. It does **not** modify any file.
- `--summary` prints a **metadata-only** report — `files_with_findings`,
  `repairable`/`suspect`/`total` counts, and a breakdown by generic reason code.
  It contains no stored text and no paths, so it is safe to paste into an audit
  or share. Exit code is `1` when suspect (unrepairable) fields remain, else `0`.

Both modes are guaranteed not to mutate files
(`tests/test_encoding_scan_safety.py::test_dry_run_scan_does_not_mutate_files`),
and the summary is guaranteed leak-free
(`tests/test_encoding_scan_safety.py::test_summary_is_metadata_only_no_body_or_path`).

## 2. Repair (opt-in, backed up by default)

Only after reviewing the dry-run:

```bash
engram repair-encoding --apply              # repairs high-confidence mojibake, with backup
engram repair-encoding --apply --no-backup  # skip backup (only if you have your own)
```

- Repair is **never** the default and only fixes high-confidence, reversible
  cases. Suspect/unrepairable fields are reported for manual review, never
  auto-changed.
- With backup (the default), originals are copied to
  `<engram-root>/backups/encoding_repair_<timestamp>/` before any write.

## 3. Notes

- The scan honours the active Engram root (`ENGRAM_DIR`, else `~/.engram`,
  legacy `~/.piia`) — see `docs/runbooks/data-sovereignty-audit.md`.
- If text looks garbled in a terminal but the scan reports clean, the issue is
  display encoding, not stored data. On PowerShell use
  `Get-Content -Encoding utf8` for UTF-8 files.
