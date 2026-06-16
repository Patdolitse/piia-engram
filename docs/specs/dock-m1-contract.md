# Dock M1 Contract

Status: **implemented backend contract, not a standalone desktop GUI.**

Dock M1 is the local owner-console surface for Piia Engram. It gives a desktop
client stable CLI JSON contracts for reading, searching, reviewing, editing, and
backing up the owner's local AI work identity store.

## Goals

- Give a GUI client a predictable home/status payload.
- Keep read-only Dock actions genuinely zero-write.
- Separate zero-write reads, owner-confirmed writes, and sensitive backup export.
- Surface governance state and system-derived labeling without turning either
  into caller-certified trust.

## Non-goals

- Dock M1 is not a packaged desktop application yet.
- Dock M1 does not change governance defaults; `ENGRAM_GOVERNANCE` remains off
  unless the owner enables it.
- Dock M1 does not authenticate callers. `caller_source` and
  `initiation_source` are advisory audit labels only.
- Dock M1 does not run remote sync, publish, push, tag, deploy, or registry
  actions.

## Command Classes

| Class | Commands | Store behavior |
|---|---|---|
| Zero-write reads | `dock-status`, `dock-resume`, `dock-search`, `dock-list`, `dock-portrait`, `dock-archived`, `dock-get-lang`, `dock-onboard-scan` | Must not mutate the Engram store root, update checks, audit stamps, migrations, indexes, or session state. |
| Owner-confirmed writes | `dock-archive`, `dock-restore`, `dock-onboard-commit`, `dock-update`, `dock-set-lang` | Deliberate local writes after the owner confirms the action in the client. |
| Sensitive export | `dock-export` | Writes a full JSON backup file. Treat the output as sensitive. |

Every JSON response uses `ok: true|false`. Error responses use
`ok: false` plus `error` and keep the same broad shape as the successful
payload when practical.

## `dock-status`

`engram dock-status --json` is the Dock home-screen payload.

It returns:

- `ok: true`
- `read_only: true`
- `dock_contract_version: "M1"`
- `engram_dir`: the selected local store root
- `dock_capabilities`: the command classes above
- `status`: the same metadata-only status object used by `engram status`

The status object may include local paths because Dock is a local owner-run
surface, but it must not include stored lesson bodies, decision reasoning,
playbook steps, API keys, secrets, or raw session content.

When Dock calls this command and no provenance labels are already set, it sets
only the caller surface default:

- `ENGRAM_CALLER_SOURCE=desktop_dock`

`ENGRAM_INITIATION_SOURCE` remains `unknown` unless the client explicitly sets a
more precise value such as `human`, `automation`, or `scheduled`. These labels
appear in governance metadata and receipts for explanation only. They never
raise sensitivity ceilings or grant write access.

## Memory Rows

`dock-list` and `dock-search` return owner-editable rows with:

- `kind`
- `id`
- `title`
- `tier`
- `copy`
- `fields`
- optional `labeling`

`labeling` is system-derived and projected as metadata only:

- `source_kind`
- `annotation_quality`
- `validation_state`
- `signals`

The client must treat `labeling` as an explanation of maturity, not as a value
to write back. Agent-facing write paths strip caller-supplied `labeling`, `tier`,
`memory_state`, `approval_status`, and `approval_required`; the storage layer
re-derives them from provenance, risk, tier, and owner-review state.

## Validation

The contract is covered by:

- `tests/test_dock_resume.py`: zero-write Dock commands, `dock-status`, row
  shapes, and labeling projection.
- `tests/test_data_labeling.py`: caller-supplied maturity labels cannot be
  smuggled through single or batch write paths.
- `tests/test_setup_wizard.py`: status governance visibility, including
  advisory source labels.
