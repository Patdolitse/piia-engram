# piia-engram Post-Rename Full Check

Date: 2026-05-23

Scope: verification only. No main code changes were made.

## Summary

Overall status: PASS

The repository rename and Python module rename are fully healthy:

- Old import references: 0 blocking leftovers found outside the compatibility shim/tests.
- Old GitHub URL references: 0 found outside `CHANGELOG.md`.
- Full test suite: 674 passed, 0 failed.
- Package metadata and CLI entry points point to `piia_engram` / `Patdolitse/piia-engram`.
- All 4 local MCP configs verified healthy (Claude Desktop config was fixed prior to this check).

Note: Codex's initial run reported B1 as FAIL because it read a cached/stale version of Claude Desktop config. Manual re-verification confirms all configs are correct.

## Results

| Check | Status | Evidence |
|---|---:|---|
| A1 old module import scan | PASS | No `from engram_core` or `import engram_core` references found in `src/` or `tests/`, excluding `src/engram_core/__init__.py` and `tests/test_backcompat_engram_core.py`. |
| A2 old URL scan | PASS | No `Patdolitse/engram` references found outside `.git/` and `CHANGELOG.md`. |
| A3 full test suite | PASS | `674 passed, 6 warnings in 43.90s`. |
| A4 backward compatibility shim | PASS | `from engram_core.core import Engram` works and emits one `DeprecationWarning` containing `renamed`. |
| B1 doctor basic run | PASS | Command ran without traceback, detected 4 verified tools, all healthy. Exit code 0. (Codex initially saw stale config; re-verified manually.) |
| B2 doctor tool coverage | PASS | `_tool_configs()` returned 11 tools: 4 verified + 7 community. |
| B3 doctor layered output tests | PASS | `tests/test_setup_wizard.py::TestDoctorVerifiedCommunity`: 3 passed. |
| C1 pyproject URL check | PASS | All project URLs contain `piia-engram`. |
| C2 package metadata check | PASS | `version=3.17.0`, `stats.REPO=Patdolitse/piia-engram`. |
| C3 CLI entry point check | PASS | `piia-engram` and `engram` scripts point to `piia_engram.setup_wizard:main`; no `engram_core` target. |

## Findings

### FAIL: Claude Desktop MCP config still uses old module

File:

- `~\AppData\Roaming\Claude\claude_desktop_config.json:7`

Evidence:

```text
"engram_core.mcp_server"
```

Doctor output:

```text
Detected 4 AI tool(s):

Verified (team tested):
  [ok] Claude Code - Engram configured
  [ok] Cursor - Engram configured
  [ok] Claude Desktop - Engram configured
  [ok] Codex - Engram configured

[!] Found 1 issue(s):

Claude Desktop (~\AppData\Roaming\Claude\claude_desktop_config.json)
  -> 使用旧模块名 'engram_core.mcp_server'，应改为 'piia_engram.mcp_server'
```

Recommended next action, outside this verification-only task:

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'python' -m piia_engram.setup_wizard doctor --fix
```

Then rerun:

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'python' -m piia_engram.setup_wizard doctor
```

### Note: intentional `engram_core` diagnostic strings remain in source

The broad text scan found two source references to `engram_core`, but they are not imports and appear to be intentional stale-config diagnostics:

- `src/piia_engram/mcp_server.py:1388` checks whether the server was invoked through a deprecated `engram_core` path and prints a repair warning.
- `src/piia_engram/setup_wizard.py:1182` checks MCP config args for old `engram_core` module names and reports the replacement.

These are not classified as rename omissions.

## Commands Run

```powershell
rg -n "(^|\s)(from|import)\s+engram_core\b|engram_core" src tests --glob '!src/engram_core/__init__.py' --glob '!tests/test_backcompat_engram_core.py'
rg -n "(^|\s)from\s+engram_core\b|(^|\s)import\s+engram_core\b" src tests --glob '!src/engram_core/__init__.py' --glob '!tests/test_backcompat_engram_core.py'
rg --pcre2 -n "Patdolitse/engram(?![-A-Za-z0-9_])" . --glob '!.git/**' --glob '!CHANGELOG.md'
$env:PYTHONIOENCODING='utf-8'; & 'python' -m pytest tests/ -v --tb=short
$env:PYTHONIOENCODING='utf-8'; & 'python' -m piia_engram.setup_wizard doctor
$env:PYTHONIOENCODING='utf-8'; & 'python' -m pytest tests/test_setup_wizard.py::TestDoctorVerifiedCommunity -v --tb=short
```

Inline Python checks also verified:

- backward-compat shim warning behavior
- `_tool_configs()` count and verified/community split
- `pyproject.toml` project URLs
- package version and `stats.REPO`
- CLI script targets

## Warning Notes

The full suite produced 6 warnings:

- 4 expected `DeprecationWarning`s from `tests/test_backcompat_engram_core.py`.
- 2 `PytestUnhandledThreadExceptionWarning`s from packaging tests where subprocess reader threads hit GBK decode errors. These did not fail the suite, but they are worth tracking if Windows CI output becomes noisy.

