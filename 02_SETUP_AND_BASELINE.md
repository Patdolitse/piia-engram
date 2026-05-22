# Phase 3 — Setup and Baseline

## Package Configuration

**File:** `pyproject.toml`
- Build backend: `setuptools.build_meta`
- Package location: `src/engram_core/`
- Requires Python >= 3.10
- Package name on PyPI: `piia-engram`
- Version: **3.16.1**

### Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `mcp` | >=1.0 | Model Context Protocol server |
| `portalocker` | >=2.0 | Cross-platform file locking |

### Optional dependency groups

| Group | Packages | Purpose |
|---|---|---|
| `dev` | `pytest>=7.0`, `tomli>=2.0` (Python <3.11) | Testing |
| `remote` | `uvicorn>=0.20` | SSE transport for remote deployment |
| `secure` | `cryptography>=41.0` | AES-256-GCM field-level encryption |
| `all` | `uvicorn`, `cryptography` | Full installation |

### Test configuration (pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

---

## Installation

**Install command (used):**
```bash
uv pip install -e /root/oss-pr-campaign/repos/engram
```

**Result:**
```
Using Python 3.11.15 environment at: /usr/local/lib/hermes-agent/venv
Resolved 31 packages in 483ms
  Building piia-engram @ file:///root/oss-pr-campaign/repos/engram
     Built piia-engram @ file:///root/oss-pr-campaign/repos/engram
Prepared 2 packages in 1.01s
Installed 2 packages in 2ms
 + piia-engram==3.16.1 (from file:///root/oss-pr-campaign/repos/engram)
 + portalocker==3.2.0
```

---

## Test Collection

**Command:** `uv run pytest --co`

**Output:**
```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.2, pluggy-1.6.0
rootdir: /root/oss-pr-campaign/repos/engram
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.6.0, xdist-3.8.0, split-0.11, langsmith-0.8.5
asyncio: mode=Mode.STRICT
collected 657 items
```

**Note:** README.md and docs/comparison.md claim "490 tests". The actual collected count is **657**. This discrepancy needs to be corrected (see `04_QUALITY_AUDIT.md`).

**Test files (10):**
- `tests/test_audit.py`
- `tests/test_core.py`
- `tests/test_crypto.py`
- `tests/test_mcp_coverage.py`
- `tests/test_mcp_tools.py`
- `tests/test_packaging.py`
- `tests/test_reconcile.py`
- `tests/test_review_page_xss.py`
- `tests/test_setup_wizard.py`
- `tests/test_stats.py`
- `tests/test_storage.py`
- `tests/test_telemetry.py`

---

## Test Execution

**Command:** `uv run pytest`

**Full output (tail):**
```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.2, pluggy-1.6.0
rootdir: /root/oss-pr-campaign/repos/engram
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.6.0, xdist-3.8.0, split-0.11, langsmith-0.8.5
asyncio: mode=Mode.STRICT
collected 657 items

tests/test_audit.py ....                                                 [  0%]
tests/test_core.py ..................................................... [  8%]
........................................................................ [ 19%]
........................................................................ [ 30%]
.....................................                                    [ 36%]
tests/test_crypto.py ...........................                         [ 40%]
tests/test_mcp_coverage.py ............................................. [ 47%]
........                                                                 [ 48%]
tests/test_mcp_tools.py ................................................ [ 55%]
.................                                                        [ 58%]
tests/test_packaging.py ......................                           [ 61%]
tests/test_reconcile.py ................................................ [ 68%]
..................................                                       [ 74%]
tests/test_review_page_xss.py ..........                                 [ 75%]
tests/test_setup_wizard.py ............................................. [ 82%]
................................................                         [ 89%]
tests/test_stats.py .................                                    [ 92%]
tests/test_storage.py ..............                                     [ 94%]
tests/test_telemetry.py ....................................             [100%]

============================= 657 passed in 20.38s =============================
```

**Result: ALL 657 TESTS PASSED** ✅

---

## Lint (ruff)

**Command:** `uv run ruff check .`

**Output:**
```
E402 Module level import not at top of file
  --> demos/cross_tool_demo.py:25:1
F541 f-string without any placeholders
  --> demos/cross_tool_demo.py:101:11
```

Only 2 issues in `demos/cross_tool_demo.py` — the main `src/` and `tests/` are clean.

---

## Type Checking (mypy)

**Command:** `uv run mypy src`

**Result:**
```
error: Failed to spawn: `mypy`
  Caused by: No such file or directory (os error 2)
```

`mypy` is not installed. Not a blocker — no type errors confirmed absent either way.

---

## Summary Table

| Check | Command | Result |
|---|---|---|
| Install | `uv pip install -e .` | ✅ Success — piia-engram==3.16.1 |
| Test collection | `uv run pytest --co` | ✅ 657 items collected |
| Test execution | `uv run pytest` | ✅ 657 passed in 20.38s |
| Lint | `uv run ruff check .` | ⚠️ 2 issues in `demos/` (non-critical) |
| Type check | `uv run mypy src` | ⏭️ Not installed |