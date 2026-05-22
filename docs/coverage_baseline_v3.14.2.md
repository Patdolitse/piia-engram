# Coverage Baseline — v3.14.2

Run on: 2026-05-22
Test count: **386 passed**
Total coverage: **78%** (3367 statements, 729 uncovered)

## Per-module coverage

| Module | Stmts | Miss | Cover | Status |
|--------|-------|------|-------|--------|
| `crypto.py` | 73 | 2 | **97%** | ✅ Strong |
| `stats.py` | 76 | 4 | **95%** | ✅ Strong |
| `compat.py` | 177 | 10 | **94%** | ✅ Strong |
| `retrieval.py` | 341 | 27 | **92%** | ✅ Strong |
| `reports.py` | 443 | 42 | **91%** | ✅ Strong |
| `audit.py` | 21 | 2 | **90%** | ✅ Strong |
| `reconcile.py` | 232 | 28 | **88%** | ✅ Strong |
| `core.py` | 668 | 94 | **86%** | ✅ Strong |
| `storage.py` | 94 | 19 | **80%** | ✅ Acceptable |
| `context.py` | 308 | 93 | **70%** | ⚠ Acceptable |
| `setup_wizard.py` | 465 | 194 | **58%** | ⚠ Interactive — see below |
| `mcp_server.py` | 466 | 214 | **54%** | ⚠ Server surface — see below |
| **TOTAL** | **3367** | **729** | **78%** | — |

## Gap analysis

### Strong (≥85%) — 8 modules

The data layer (`crypto`, `compat`, `retrieval`, `reports`, `reconcile`, `core`, `audit`) is well-covered. These are the modules with the highest mutation/risk surface.

### Acceptable (70–85%) — 2 modules

- `storage.py` (80%) — uncovered branches are mostly fs-error fallback paths (read failures, lock-timeout handling) which are hard to trigger deterministically.
- `context.py` (70%) — uncovered: `extract_knowledge` LLM branch (requires a live provider), some `generate_context` reconcile-on-cold-start try/except paths.

### Below 70% — interactive surfaces

**`setup_wizard.py` (58%)** — primarily the interactive `engram setup` flow:
- `prompt_for_role()`, `prompt_for_language()` — wait on stdin input
- Branch logic for "first run vs. upgrade" detection
- Bilingual i18n switching

These can be lifted to ~75% by mocking `input()` and `Path.exists()` in unit tests. **Recommended for v3.15.0**.

**`mcp_server.py` (54%)** — primarily the SSE transport + uncalled tool wrappers:
- SSE/HTTP transport setup (`run_sse_async`, CORS middleware, TokenAuthMiddleware)
- ~20 tool wrappers we haven't unit-tested yet (only ~25 of ~45 tools covered in v3.14.2)
- Argument parsing for CLI variants

Lifting MCP coverage requires:
1. Add more tool wrapper tests (cheap, mechanical) — see v3.14.2 `test_mcp_tools.py` for the pattern
2. Add a stdio/SSE integration test (`test_mcp_e2e.py`) — deferred to v3.15.0

## How to reproduce

```bash
# Run tests with coverage
python -m coverage run -m pytest tests/

# Console report
python -m coverage report --sort=cover

# Browsable HTML
python -m coverage html -d docs/coverage
# Open docs/coverage/index.html
```

Configuration: see [`.coveragerc`](../.coveragerc) at repo root.

## Trend

| Version | Tests | Total coverage |
|---------|-------|----------------|
| v3.13.2 | 327 | (not measured) |
| v3.14.0 | 328 | (not measured) |
| v3.14.1 | 329 | (not measured) |
| **v3.14.2** | **386** | **78%** |

This is our first published coverage baseline. Future PRs should not regress below 75% total; modules currently ≥85% should not regress below 80%.
