# Runbook: New-user install & launch truth audit

Status: **reproducible install/launch audit.** This documents what a brand-new
user actually gets after `pip install`, which launch paths are real, and what is
only *expected to work* so we never overclaim integration. Where a claim can be
machine-checked it is backed by a test; where it depends on a third-party GUI it
is labelled as expected-to-work.

## 1. Install

```bash
pip install piia-engram
engram setup
```

`engram setup` is an interactive wizard. It **detects** installed MCP-capable
tools and reads their configs **read-only by default**. It writes to an external
client config only when you explicitly opt in:

```bash
engram setup --apply-external-config
```

It backs up any file before changing it. See
`docs/runbooks/setup-upgrade-safety.md`.

## 2. Launch paths (what is real)

| Launch path | Source of truth | Verified by |
| --- | --- | --- |
| `piia-engram` (CLI) | `[project.scripts]` -> `piia_engram.setup_wizard:main` | `tests/test_install_entrypoints.py` |
| `piia-engram-mcp` (MCP server) | `[project.scripts]` -> `piia_engram.mcp_server:main` | `tests/test_install_entrypoints.py` |
| `engram` (back-compat alias) | `[project.scripts]` -> `piia_engram.setup_wizard:main` | `tests/test_install_entrypoints.py` |
| `python -m piia_engram.mcp_server` | module `main()` | `tests/test_install_entrypoints.py` |
| `uvx --from piia-engram piia-engram-mcp` | same entry point, zero pre-install | expected-to-work (depends on `uv`) |

The test resolves every declared console script to an importable callable, so a
rename or removal fails the audit before a user hits a broken command.

## 3. MCP client config expectations

Setup auto-detects and, with opt-in, configures these clients. Each is
configured at that client's own MCP config location; Engram never writes there
silently.

| Client | Config kind | Status |
| --- | --- | --- |
| Claude Code | per-user MCP config (JSON) | evidence-tracked setup path |
| Claude Desktop | desktop MCP config (JSON) | evidence-tracked setup path |
| Cursor | per-user MCP config (JSON) | evidence-tracked setup path |
| Codex | per-user config (TOML) | evidence-tracked setup path |
| Windsurf / Trae / Cline / others | per-user MCP config (JSON) | expected/community setup path |

Evidence-tracked setup path means the config shape and launch path are covered
by maintainer evidence. Expected/community setup path means the path is
generated from known MCP config patterns but not routinely exercised. This audit
does **not** assert that any GUI renders Engram output a particular way; it only
asserts that the launch command and config are produced correctly.

## 4. First-run health check

```bash
engram doctor          # read-only health report
engram doctor --fix    # opt-in repair of stale/missing MCP entries
```

On a fresh install `engram doctor` (read-only) reports, in one pass:

- detected AI tools and which have Engram configured;
- functional checks: `piia_engram.core` importable, Engram initialises at the
  active root, identity/quick-context presence, MCP tool registration;
- terminal/runtime encoding sanity.

It also emits a data-fragmentation warning through the runtime log, on Engram
init, if knowledge exists under more than one root.

It honours `ENGRAM_DIR` for the active root and performs no external writes
without `--fix`. See `docs/runbooks/data-sovereignty-audit.md` for the
path-origin guarantees.

## 5. Reproduce the audit locally

```bash
# Entry-point / launch-path truth (no network, no writes):
python -m pytest tests/test_install_entrypoints.py -q

# First-run doctor against a throwaway root (does not touch real data):
ENGRAM_DIR=$(mktemp -d) engram doctor
```

## 6. What this audit deliberately does NOT claim

- It does not claim a specific GUI visually integrates Engram beyond launching
  the MCP server via the generated config.
- It does not claim expected/community clients are regression-tested.
- It does not run a real publish/network action.
