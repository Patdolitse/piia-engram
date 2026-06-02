# Cursor plugin + Engram skill — local validation runbook

Status: **skeleton, not published.** This runbook is the manual checklist to run
against a live Cursor install before the `.cursor-plugin/` skeleton is ever
proposed for publication. The automated guards in
`tests/test_ecosystem_entrypoints.py` cover format and honest-positioning drift;
they do **not** prove the plugin loads in Cursor. That requires the manual steps
below.

## 0. Preconditions

- Engram installed so the MCP entry point is on PATH:
  ```bash
  pip install piia-engram
  # or, no install:
  # uvx --from piia-engram piia-engram-mcp
  ```
- Confirm the command resolves:
  ```bash
  piia-engram-mcp --help    # should start / print usage, not "command not found"
  ```

## 1. Static checks (runnable now, no Cursor)

```powershell
$py = "E:\Temp\engram-v337-pypi-smoke\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path ".\src").Path
& $py -m pytest tests/test_ecosystem_entrypoints.py -q -p no:cacheprovider
```

Expected: all pass. These verify:

- `.cursor-plugin/plugin.json` parses, is named `engram`, matches Cursor's name
  pattern, declares `mcpServers` wired to `piia-engram-mcp`, and points at the
  skill.
- `skills/engram/SKILL.md` opens with valid frontmatter, has `name: engram`, a
  trigger-rich description within the 1024-char budget, and links its reference
  files.
- Neither the skill nor the Cursor copy claims unimplemented features
  (`workflow_stage`, `caller_role`, `caller_depth`, `stop-hook`,
  `passive writeback`, `acp`) or marketing overclaims.

## 2. Live Cursor checks (require a Cursor install — DO when validating)

| # | Check | How | Pass criteria |
|---|-------|-----|---------------|
| 2.1 | Plugin is discovered | Place/symlink the plugin where Cursor scans plugins; reload | `engram` appears in Cursor's plugin/MCP list |
| 2.2 | MCP server launches | Open the MCP panel after enabling | `engram` server shows connected; no spawn error for `piia-engram-mcp` |
| 2.3 | **Skill path resolves** | Check whether the `engram` skill is loaded | Skill guidance is available. If NOT, this is the known `../skills/` seam (see below) |
| 2.4 | Tier-1 tools visible | Inspect exposed tools | Core tools (`get_resume_brief`, `add_lesson`, `search_knowledge`, …) are listed |
| 2.5 | `ENGRAM_TOOLS=all` | Add env to the server block, reload | Full tool set appears |
| 2.6 | Read is safe | Call `get_user_context` / `get_resume_brief` | Returns local context; no network, no write |
| 2.7 | Write is user-driven | Call `add_lesson` | Entry lands in the local store; nothing auto-promoted to verified |

## 3. Known seams to validate (documented in `.cursor-plugin/README.md`)

- **`skills` path resolution** — `plugin.json` sets `"skills": "../skills/"` so
  it resolves to the repo's top-level `skills/` from `.cursor-plugin/`. If
  Cursor resolves plugin-relative paths differently, change to `./skills/` and
  copy the skill into the plugin directory. Record the working form here after
  2.3.
- **`mcpServers` inline vs `mcp.json`** — declared inline. If the live Cursor
  version expects a separate `mcp.json`, move the block and re-run 2.1–2.2.
- **`version`** — `0.1.0`, intentionally decoupled from the Engram package
  version. Confirm Cursor does not require it to match the server's version.

## 4. Record results

After a live run, fill this in (do not publish until all live checks pass):

```text
date:
cursor_version:
plugin_discovered (2.1):        pass/fail + note
mcp_launches (2.2):             pass/fail + note
skill_path_form_that_worked:    ../skills/  |  ./skills/  |  other
tier1_tools_visible (2.4):      pass/fail
read_safe (2.6):                pass/fail
write_user_driven (2.7):        pass/fail
blocking_issues:
```

## 5. Do NOT, in this runbook

- Do not publish to any marketplace or registry.
- Do not bundle credentials or write to a user's config without consent.
- Do not add capability claims to plugin/skill copy that aren't implemented —
  the overclaim tests will (intentionally) fail.
