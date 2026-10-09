# Non-interactive setup and JSON contract (v1)

```sh
pip install piia-engram
engram setup --non-interactive                  # inspect the plan first
engram setup --non-interactive --apply          # explicitly authorize that plan
engram setup --non-interactive --clients cursor,codex --json
engram setup --non-interactive --apply --clients cursor,codex --lang en --json
engram doctor --json
```

No prompts are displayed and stdin is never read. `--lang zh|en` defaults to
`en` and selects the output language; it does not create an identity preference.
Without `--apply`, setup only detects clients and renders configuration changes
in memory. It writes nothing to the store, home, client configs, or caches, and
runs no client commands, update checks, or telemetry.

`--apply` configures only MCP connections. It does not initialize identity or
knowledge files, import other tools' memories, approve proposals, inject
instruction files, install session hooks, or clean up Claude Code's legacy
configuration. Identity and knowledge stay empty or untouched. A missing store
may acquire only configuration backups and a metadata-only file-safety ledger;
the MCP server initializes the store when the client later starts it.

File-based clients use the existing JSON/TOML merge, validation, backup and
atomic-write helpers. Existing tool modes, owner environment entries and strict
approval settings are preserved; a new entry retains the wizard's `all` tool
mode default. Unparseable configs stay unchanged. Each changed existing config
is backed up under `<store>/backups/file_safety/external/`; the existing per-file
backup retention applies. The ledger may rotate to `file_safety_ledger.jsonl.1`.
Those bounded side effects are included in the plan's store path patterns.
Config paths that resolve inside the store are refused, including symlink aliases.

Claude Code is registered through the existing `claude mcp add --scope user`
function. Setup never writes its user config directly. If the CLI is unavailable,
unsafe to invoke, fails, or a differing/conflicting entry must be resolved, setup
reports a manual step. An identical entry is left alone. An unreadable user config
requires a manual step without a CLI probe. The existing size cap also applies
to Claude Code's user and legacy configs, without reading oversized bodies.
Command output and config values are
never printed. Command strings are **redacted templates**, not paste-ready commands:
replace angle-bracket placeholders locally with the installed Python executable,
store, tool mode and encoding, and retain any existing local environment entries
(including strict approval). Consult the [Claude Code guide](../integrations/claude-code.md).

`--clients` accepts comma-separated IDs: `claude_code`, `cursor`, `claude_desktop`,
`codex`, `windsurf`, `trae`, `codebuddy`, `copilot_vscode`, `cline`, `roo_code`,
`amazon_q`, `augment`, `zed`. By default all detected clients are selected.
Detection follows the wizard's existing directory/config detection. An explicitly
selected but undetected client requires a manual step; setup does not create its
config. Other clients are excluded. Unknown IDs and empty list elements are usage
errors. The new options require `--non-interactive`; they cannot be combined with
`--advanced` or `--apply-external-config`. The interactive wizard remains available.

Within each invocation, setup renders a plan before applying it. File writes use
the exact rendered text; if a target config, resolved location or strict-approval
state changes before apply, that client is
left for manual review. A later invocation recomputes the plan from current files.
This is a per-client operation, not a transaction across clients; successful
changes are not rolled back when another client requires attention.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Plan or apply succeeded; no selected client requires a manual step. No detected clients is also a successful empty plan. |
| 1 | Partial: a selected client requires a manual step, a write/registration failed, or the store location is invalid. Applies to plan and apply. |
| 2 | Invalid command-line usage. No writes or client commands. |

## JSON v1

`--json` emits exactly one JSON object on stdout, with no additional notices.
The object has `schema_id: "piia-engram/setup"` and integer `schema_version: 1`.
Consumers should use those fields to select a parser; incompatible changes require
a new schema version. Object key order is not part of the contract.

| Field | Type / values |
| --- | --- |
| `mode` | `plan` or `apply` |
| `language` | `en` or `zh` |
| `result` | `ok`, `partial`, or `usage_error` |
| `exit_code` | integer 0, 1, or 2; matches process exit |
| `store.path` | shortened path label |
| `store.status` | `missing`, `directory`, or `invalid`; after apply reflects the resulting location |
| `store.writes` | array of `{kind, path}`; `kind` is `ledger` or `backups`; paths are bounded patterns for possible side effects |
| `clients` | array of client records, including excluded/undetected clients; empty on usage error |
| `next_steps` | array of human-readable instructions; clients must not execute these strings automatically |

Each client record always contains:

| Field | Type / values |
| --- | --- |
| `id`, `name` | supported client ID and display name |
| `detected`, `selected` | booleans |
| `config_path` | shortened path label, or null when no platform path is available |
| `action` | `none`, `write`, `register`, or `manual` |
| `result` | `not_detected`, `excluded`, `planned`, `unchanged`, `manual`, `written`, `registered`, or `failed` |
| `reason` | metadata-only code: `not_detected`, `excluded`, `invalid_store`, `missing_server`, `already_registered`, `registration`, `registration_requires_manual_step`, `already_configured`, `configuration`, `config_requires_manual_step`, `config_changed`, `unsafe_config_target`, or `apply_failed` |
| `writes` | array of planned config path labels; no config content |
| `commands` | array of redacted command templates planned for Claude Code registration; empty for file-based clients |
| `manual_command` | redacted Claude Code command template when needed, otherwise null; file-based manual steps use `reason` and `next_steps` |

In apply results, `action`, `writes`, `commands` and `store.writes` describe the
authorized plan, while `result` describes what happened. A failed operation may
have already created its protective backup; the backup/ledger locations remain
within the reported patterns.

Paths under the home directory start with `~`. Paths outside home under location
overrides use `$CLAUDE_CONFIG_DIR`, `$APPDATA`, `$LOCALAPPDATA`, or `$ENGRAM_DIR`.
These are labels, not shell expressions to execute. Output contains no environment
values, config values, secrets, raw exceptions, or raw CLI stdout/stderr.

Example (fictional home, one detected client; other records omitted for brevity):

```json
{
  "schema_id": "piia-engram/setup",
  "schema_version": 1,
  "mode": "plan",
  "language": "en",
  "result": "ok",
  "exit_code": 0,
  "store": {
    "path": "~/.engram",
    "status": "missing",
    "writes": [
      {"kind": "ledger", "path": "~/.engram/file_safety_ledger.jsonl*"},
      {"kind": "backups", "path": "~/.engram/backups/file_safety/external/*.bak"}
    ]
  },
  "clients": [{
    "id": "cursor", "name": "Cursor", "detected": true, "selected": true,
    "config_path": "~/.cursor/mcp.json", "action": "write",
    "result": "planned", "reason": "configuration", "writes": ["~/.cursor/mcp.json"],
    "commands": [], "manual_command": null
  }],
  "next_steps": ["Restart the configured client, then run engram doctor --json."]
}
```

An apply result for that client changes `mode` to `apply` and its `result` to
`written`. No field contains identity or knowledge content.
