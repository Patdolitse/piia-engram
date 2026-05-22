# Coverage Baseline — v3.16.1

Run on: 2026-05-22
Test count: **657 passed**
Total coverage: **96%** (3775 statements, 163 uncovered)

## Per-module coverage

| Module | Stmts | Miss | Cover | Status |
|--------|-------|------|-------|--------|
| `reports.py` | 6 | 0 | **100%** | ✅ Strong |
| `__init__.py` | 3 | 0 | **100%** | ✅ Strong |
| `reports_identity.py` | 65 | 0 | **100%** | ✅ Strong |
| `stats.py` | 99 | 0 | **100%** | ✅ Strong |
| `storage.py` | 94 | 0 | **100%** | ✅ Strong |
| `mcp_server.py` | 487 | 7 | **99%** | ✅ Strong |
| `context.py` | 308 | 1 | **99%** | ✅ Strong |
| `reconcile.py` | 253 | 4 | **98%** | ✅ Strong |
| `crypto.py` | 76 | 2 | **97%** | ✅ Strong |
| `reports_review.py` | 138 | 4 | **97%** | ✅ Strong |
| `core.py` | 668 | 36 | **95%** | ✅ Strong |
| `reports_analytics.py` | 211 | 10 | **95%** | ✅ Strong |
| `reports_rarity.py` | 38 | 2 | **95%** | ✅ Strong |
| `compat.py` | 177 | 10 | **94%** | ✅ Strong |
| `setup_wizard.py` | 647 | 46 | **93%** | ✅ Strong |
| `telemetry.py` | 143 | 12 | **92%** | ✅ Strong |
| `retrieval.py` | 341 | 27 | **92%** | ✅ Strong |
| `audit.py` | 21 | 2 | **90%** | ✅ Strong |
| **TOTAL** | **3775** | **163** | **96%** | — |

## Changes since v3.14.2

| Metric | v3.14.2 | v3.16.1 | Delta |
|--------|---------|---------|-------|
| Tests | 386 | 657 | **+271** |
| Total coverage | 78% | 96% | **+18%** |
| Statements | 3367 | 3775 | +408 |
| Uncovered | 729 | 163 | **-566** |

### Biggest movers (from v3.14.2)

- `mcp_server.py`: 54% → **99%** (+45%) — MCP tool tests, wrap_up_session error paths, read_web_content
- `reconcile.py`: (not measured) → **98%** — authorization, file errors, frontmatter, decode edge cases
- `context.py`: 70% → **99%** (+29%) — all generate_context sections, extract_knowledge
- `setup_wizard.py`: 58% → **93%** (+35%) — run_setup, auto_migrate, CLI routing, choice/prompt functions
- `core.py`: 86% → **95%** (+9%) — schema migration, eviction, link/merge, import overwrite
- `storage.py`: 80% → **100%** (+20%) — legacy fallback, lock timeout, parse_iso
- `stats.py`: 73% → **100%** (+27%) — log_stats, main, daily clones
- `reports_identity.py`: 83% → **100%** (+17%) — all identity card sections

### All 18 modules at ≥90%

Every module in the codebase is now at 90% coverage or above.

## Remaining gaps (hard-to-reach code)

- `mcp_server.py` (7 miss) — ImportError fallback imports (unreachable in test env)
- `setup_wizard.py` (46 miss) — platform-specific detection, advanced mode I/O, identity card preview
- `core.py` (36 miss) — decision eviction staging→verified, bidirectional link migration edge
- `retrieval.py` (27 miss) — complex retrieval scoring edge cases
- `telemetry.py` (12 miss) — config path edge cases
- `compat.py` (10 miss) — format-specific edge cases

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
| **v3.16.1+** | **657** | **96%** |

Regression floor: 93% total. Modules currently ≥95% should not regress below 90%.
