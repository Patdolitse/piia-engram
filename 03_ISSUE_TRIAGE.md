# Phase 4 — Issue & PR Triage

## GitHub API Investigation

### API Responses

**Open Issues:** `gh issue list --repo Patdolitse/engram --state open`
```
(total: 0 open issues)
```

**Open PRs:** `gh pr list --repo Patdolitse/engram --state open`
```
(total: 0 open pull requests)
```

**Merged PRs:** `gh pr list --repo Patdolitse/engram --state merged`
```
(total: 0 merged PRs — all responses empty)
```

### API Verification

Confirmed repository exists and is accessible:
```json
{
  "id": 1242620513,
  "name": "engram",
  "full_name": "Patdolitse/engram",
  "private": false,
  "owner": { "login": "Patdolitse" },
  "html_url": "https://github.com/Patdolitse/engram",
  "description": "AI identity layer for Claude Code, Codex and Cursor — ...",
  "stargazers_count": 61,
  "watchers_count": 61,
  "forks_count": 2,
  "open_issues_count": 0,
  "default_branch": "main",
  "has_issues": true,
  "has_discussions": true,
  "created_at": "2026-05-18T15:38:43Z",
  "updated_at": "2026-05-22T13:30:46Z",
  "pushed_at": "2026-05-22T13:30:41Z"
}
```

Auth status: `gh auth status` → "✓ Logged in to github.com account okwn"

### Conclusion

The repository has **0 open issues**, **0 open PRs**, and **0 merged PRs** returned via the GitHub API. This is consistent with a project that is very new (created 2026-05-18) and has not yet received community contributions — the owner drives development directly via Claude Code and Codex assistance.

---

## Internal Triage from Code & Docs

Since there are no GitHub issues to triage, issues and PR candidates must be derived from the codebase itself. The v3.16.0 cross-AI evaluation report (`experiments/evaluations/v3.16.0/cross_ai/REPORT.md`) and the CHANGELOG.md serve as the primary sources for actual problems and planned work.

### Cross-AI Evaluation Issues (from `cross_ai/REPORT.md`)

| # | Issue | Severity | File | Recommended Action |
|---|---|---|---|---|
| 1 | Telemetry payload validator ignores dictionary keys | medium | `src/engram_core/telemetry.py:139-155` | Validate dict keys, enforce tool name allowlist before Phase 2 |
| 2 | Usage statistics only track Tier-1 tools, leaving 33 tools untracked | medium | `src/engram_core/mcp_server.py:61-70` | Centralize tracking via decorator, or document "Tier-1 only" clearly |
| 3 | `wrap_up_session` flushes stats before recording its own call | medium | `src/engram_core/mcp_server.py:1216-1235` | Move `_track("wrap_up_session")` before flush |
| 4 | Documentation stale: `CONTRIBUTING.md:28` says no telemetry | low | `CONTRIBUTING.md` | Update to reflect opt-in telemetry |
| 5 | Documentation stale: `docs/comparison.md:34` says 386 tests / 78% | low | `docs/comparison.md` | Update to 657 tests / 83% |
| 6 | Documentation stale: `docs/telemetry_roadmap.md:55` labels Phase 2 as v3.16.0 | low | `docs/telemetry_roadmap.md` | Correct Phase 2 version label |
| 7 | Documentation stale: `docs/architecture.md` line counts now incorrect | low | `docs/architecture.md:77-80` | Refresh module line counts |
| 8 | README.md says `core.py` is 1088 lines, local count is 1097 | low | `README.md:349` | Update line count |

### Other Observations from Code Review

| # | Observation | Location | Type |
|---|---|---|---|
| A | `demos/cross_tool_demo.py` has ruff violations (E402 import, F541 f-string) | `demos/cross_tool_demo.py:25,101` | lint |
| B | `mcp_server.py` is 1411 lines — large for a single module | `src/engram_core/mcp_server.py` | maintainability |
| C | `_apply_tool_tier()` uses fragile `getattr` introspection on FastMCP internals | `mcp_server.py:108-124` | robustness |
| D | `read_file` tool has no file size or extension restrictions | `mcp_server.py:read_file` | security |
| E | `conftest.py` fixtures may need review re: test isolation | `tests/conftest.py` (if exists) | testing |
| F | No `mypy` pre-commit hook configured; type hints not enforced | `pyproject.toml` | tooling |

---

## Repository Metadata

| Field | Value |
|---|---|
| Created | 2026-05-18 |
| Age at analysis | ~4 days |
| Stars | 61 |
| Forks | 2 |
| Open issues | 0 |
| Open PRs | 0 |
| License | Apache-2.0 |
| Primary language | Python |
| Test count | 657 (all passing) |
| Coverage | 83% |
| MCP tools | 43 |

---

## Conclusion

The repository is young and actively developed by the owner (Patdolitse) with AI assistance. There are no community issues or PRs to act on. The immediate "issue backlog" is derived entirely from the internal cross-AI evaluation and consists primarily of documentation corrections and two medium-severity telemetry/design issues.