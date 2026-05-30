# GUI Entry Doctor Probe Design

**Goal:** Make `engram doctor` verify whether GUI MCP client entries can launch the new `piia-engram-mcp` entry point, without turning setup into a broad rewrite.

**Context:** v3.37.0 added the `piia-engram-mcp` console script and documented zero-install client configs using `uvx --from piia-engram piia-engram-mcp`. The current doctor checks config presence and stale file paths, but it does not classify entry-point style or probe whether a configured command is launchable. Users can therefore have a config that looks present yet fails when the GUI client starts it.

## Scope

This change is limited to setup/doctor diagnostics:

- Inspect configured `engram` MCP server entries in known tool config files.
- Classify recommended, compatible, legacy, and invalid command shapes.
- Run a bounded `--help` probe only for command shapes where that is safe.
- Print actionable OK/WARN/FAIL messages in `engram doctor`.
- Keep `engram doctor --fix` limited to existing safe rewrites for stale paths and legacy config shapes.

This change does not redesign `engram setup`, does not change MCP tool behavior, and does not publish a release by itself.

## Entry Shapes

Doctor should classify these shapes:

- Recommended zero-install: `command = "uvx"`, `args = ["--from", "piia-engram", "piia-engram-mcp", ...]`.
- Recommended installed entry point: `command = "piia-engram-mcp"`, optional transport args.
- Compatible module entry: `command = <python>`, `args = ["-m", "piia_engram.mcp_server", ...]`.
- Legacy direct script entry: `command = <python>`, args containing a `mcp_server.py` path.
- Invalid or incomplete entry: missing command, non-list args, unknown command, or a path-like command that does not exist.

Legacy direct script entries remain issues because direct `.py` invocation can break relative imports in spawned clients.

## Probe Rules

The probe must never start a long-running stdio server. It should build a help command from the configured entry:

- `uvx --from piia-engram piia-engram-mcp --help`
- `piia-engram-mcp --help`
- `<python> -m piia_engram.mcp_server --help`

The probe uses `subprocess.run(..., capture_output=True, text=True, timeout=5)`. A zero exit code is OK. A nonzero exit code or timeout is a doctor issue. The printed detail should include a compact reason, not full stdout/stderr dumps.

For legacy direct script entries and incomplete entries, doctor should report the config issue and skip probing.

## User Output

Configured tools should still print the existing configured/not configured status. Additional launch diagnostics should appear in the issue list when a configured entry is not recommended or cannot be probed.

Example messages:

- `[ok] Claude Code - Engram configured`
- `-> Entry point style: recommended uvx zero-install`
- `-> MCP launch probe failed: command exited with code 1`
- `-> Uses direct mcp_server.py path; use ["-m", "piia_engram.mcp_server"] or piia-engram-mcp`

The output should stay text-only and safe for Windows consoles via existing `_safe_print`.

## Testing

Tests live in `tests/test_setup_wizard.py` and should cover:

- `uvx --from piia-engram piia-engram-mcp` is classified as recommended and probes via `--help`.
- `piia-engram-mcp` is classified as recommended and probes via `--help`.
- `python -m piia_engram.mcp_server` is classified as compatible and probes via `--help`.
- Direct `mcp_server.py` path produces a legacy issue and is not probed.
- Probe timeout or nonzero return produces a doctor issue without crashing.
- Existing `doctor --fix` stale-path behavior still works.

The first implementation should run targeted tests for `tests/test_setup_wizard.py` and a publish allowlist check. Full suite can run before any push or release.

## Non-Goals

- No background process management.
- No real GUI client launch.
- No network dependency.
- No automatic conversion of `uvx` vs installed entry-point choices.
- No changes to runtime governance or MCP tool definitions.
