# Coverage Baseline — v3.16.1

Run on: 2026-05-22
Test count: **520 passed**
Total coverage: **87%** (3778 statements, 486 uncovered)

## Per-module coverage

| Module | Stmts | Miss | Cover | Status |
|--------|-------|------|-------|--------|
| `reports.py` | 6 | 0 | **100%** | ✅ Strong |
| `__init__.py` | 3 | 0 | **100%** | ✅ Strong |
| `crypto.py` | 76 | 2 | **97%** | ✅ Strong |
| `reports_review.py` | 138 | 4 | **97%** | ✅ Strong |
| `reports_analytics.py` | 211 | 10 | **95%** | ✅ Strong |
| `reports_rarity.py` | 38 | 2 | **95%** | ✅ Strong |
| `compat.py` | 177 | 10 | **94%** | ✅ Strong |
| `telemetry.py` | 143 | 12 | **92%** | ✅ Strong |
| `retrieval.py` | 341 | 27 | **92%** | ✅ Strong |
| `setup_wizard.py` | 650 | 66 | **90%** | ✅ Strong |
| `audit.py` | 21 | 2 | **90%** | ✅ Strong |
| `mcp_server.py` | 487 | 70 | **86%** | ✅ Strong |
| `core.py` | 668 | 93 | **86%** | ✅ Strong |
| `reconcile.py` | 253 | 38 | **85%** | ✅ Strong |
| `reports_identity.py` | 65 | 11 | **83%** | ✅ Acceptable |
| `storage.py` | 94 | 19 | **80%** | ✅ Acceptable |
| `stats.py` | 99 | 27 | **73%** | ⚠ Acceptable |
| `context.py` | 308 | 93 | **70%** | ⚠ Acceptable |
| **TOTAL** | **3778** | **486** | **87%** | — |

## Changes since v3.14.2

| Metric | v3.14.2 | v3.16.1 | Delta |
|--------|---------|---------|-------|
| Tests | 386 | 520 | **+134** |
| Total coverage | 78% | 87% | **+9%** |
| Statements | 3367 | 3778 | +411 |
| Uncovered | 729 | 486 | **-243** |

### Biggest movers

- `setup_wizard.py`: 58% → **90%** (+32%) — added 24 new tests covering run_setup, auto_migrate, CLI main, doctor --fix, safe_print, telemetry CLI edge cases
- `mcp_server.py`: 54% → **86%** (+32%) — added MCP coverage tests, tool wrapper tests
- `telemetry.py`: (new in v3.15) → **92%** — 36 tests covering config, payload validation, key validation, tool tracker
- Reports modules split from monolithic `reports.py` → individually tracked

### Modules at ≥85% (14 of 18)

All high-risk modules (core, mcp_server, telemetry, crypto, retrieval, reconcile) are now ≥85%.

## Remaining gaps

- `context.py` (70%) — `extract_knowledge` LLM branch (requires live provider), some reconcile-on-cold-start try/except paths
- `stats.py` (73%) — `run_stats` interactive output (mostly print formatting)
- `reports_identity.py` (83%) — edge cases in identity report generation
- `storage.py` (80%) — fs-error fallback paths (read failures, lock-timeout handling)

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
| **v3.16.1** | **520** | **87%** |

Regression floor: 82% total. Modules currently ≥85% should not regress below 80%.
