# Coverage Baseline — v3.16.1

Run on: 2026-05-22
Test count: **586 passed**
Total coverage: **93%** (3775 statements, 283 uncovered)

## Per-module coverage

| Module | Stmts | Miss | Cover | Status |
|--------|-------|------|-------|--------|
| `reports.py` | 6 | 0 | **100%** | ✅ Strong |
| `__init__.py` | 3 | 0 | **100%** | ✅ Strong |
| `reports_identity.py` | 65 | 0 | **100%** | ✅ Strong |
| `stats.py` | 99 | 0 | **100%** | ✅ Strong |
| `storage.py` | 94 | 0 | **100%** | ✅ Strong |
| `context.py` | 308 | 1 | **99%** | ✅ Strong |
| `crypto.py` | 76 | 2 | **97%** | ✅ Strong |
| `reports_review.py` | 138 | 4 | **97%** | ✅ Strong |
| `reports_analytics.py` | 211 | 10 | **95%** | ✅ Strong |
| `core.py` | 668 | 36 | **95%** | ✅ Strong |
| `reports_rarity.py` | 38 | 2 | **95%** | ✅ Strong |
| `compat.py` | 177 | 10 | **94%** | ✅ Strong |
| `telemetry.py` | 143 | 12 | **92%** | ✅ Strong |
| `retrieval.py` | 341 | 27 | **92%** | ✅ Strong |
| `audit.py` | 21 | 2 | **90%** | ✅ Strong |
| `setup_wizard.py` | 647 | 69 | **89%** | ✅ Strong |
| `mcp_server.py` | 487 | 70 | **86%** | ✅ Strong |
| `reconcile.py` | 253 | 38 | **85%** | ✅ Strong |
| **TOTAL** | **3775** | **283** | **93%** | — |

## Changes since v3.14.2

| Metric | v3.14.2 | v3.16.1 | Delta |
|--------|---------|---------|-------|
| Tests | 386 | 586 | **+200** |
| Total coverage | 78% | 93% | **+15%** |
| Statements | 3367 | 3775 | +408 |
| Uncovered | 729 | 283 | **-446** |

### Biggest movers

- `context.py`: 70% → **99%** (+29%) — added 16 tests: preferences, quality, project sections, extract_knowledge mock LLM, ingest_extraction all branches, duplicate decisions, reconcile failures
- `setup_wizard.py`: 58% → **89%** (+31%) — added 24 tests: run_setup, auto_migrate, CLI main, doctor --fix, safe_print, telemetry CLI edge cases
- `mcp_server.py`: 54% → **86%** (+32%) — MCP coverage tests, tool wrapper tests
- `core.py`: 86% → **95%** (+9%) — schema migration, field rejection, eviction overflow, link/unlink/merge, import overwrite
- `storage.py`: 80% → **100%** (+20%) — legacy fallback, corrupt JSON, lock timeout, _parse_iso edge cases
- `reports_identity.py`: 83% → **100%** (+17%) — all identity card sections
- `stats.py`: 73% → **100%** (+27%) — log_stats, main, daily clones
- `telemetry.py`: (new in v3.15) → **92%** — 36 tests covering config, payload validation, key validation, tool tracker

### Modules at ≥85% (18 of 18)

All modules are now ≥85%. All high-risk modules (core, mcp_server, telemetry, crypto, retrieval, reconcile, context) are ≥85%.

## Remaining gaps

- `reconcile.py` (85%) — complex reconciliation edge branches
- `mcp_server.py` (86%) — MCP protocol edge cases, error handlers
- `setup_wizard.py` (89%) — advanced mode paths, platform-specific detection

## How to reproduce

```bash
python -m pytest --cov=engram_core --cov-report=term-missing tests/
```

## Trend

| Version | Tests | Total coverage |
|---------|-------|----------------|
| v3.13.2 | 327 | (not measured) |
| v3.14.2 | 386 | 78% |
| v3.15.0 | 490 | 83% |
| v3.16.0 | 490 | 83% |
| v3.16.1 | 541 | 90% |
| **v3.16.1+** | **586** | **93%** |

Regression floor: 90% total. Modules currently ≥90% should not regress below 85%.
