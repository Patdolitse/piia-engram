# Contributing to piia-engram

Thanks for considering a contribution to piia-engram — the AI identity layer that stores who you are, not just what you did.

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh-CN.md)

## Architecture Overview

```
src/piia_engram/
    core.py            # Core engine: knowledge CRUD, identity, link management
    retrieval.py       # RetrievalMixin — search, ranking, tier promotion
    context.py         # ContextMixin — cold-start context, ingestion helpers
    reconcile.py       # ReconcileMixin — cross-tool memory/config sync
    reports.py         # ReportsMixin — thin hub composing 4 sub-mixins
    mcp_server.py      # MCP tool/resource definitions (60 tools, the AI-facing API)
    setup_wizard.py    # Interactive setup CLI + doctor diagnostics + instruction injection
    crypto.py          # AES-256-GCM encryption for sensitive profile fields
    telemetry.py       # Opt-in anonymous usage statistics (Phase 1: local log only)
tests/
    test_core.py           # Core engine (296 tests)
    test_setup_wizard.py   # Setup wizard + doctor + telemetry CLI + instruction injection (111 tests)
    test_reconcile.py      # Auto-sync, staging, conflict detection (82 tests)
    test_mcp_tools.py      # MCP tool wrappers (72 tests)
    test_mcp_coverage.py   # MCP wrapper coverage (56 tests)
    test_telemetry.py      # Anonymous usage statistics (55 tests)
    test_crypto.py         # AES-256-GCM encryption (27 tests)
    test_packaging.py      # Package metadata, CI, MCP tool verification (22 tests)
    test_stats.py          # GitHub/PyPI statistics (17 tests)
    test_storage.py        # Storage primitives (14 tests)
    test_contexts.py       # Context management (14 tests)
    test_review_page_xss.py # XSS prevention in review page (10 tests)
    test_backcompat_engram_core.py # Backward compatibility (5 tests)
    test_audit.py          # Audit logging (4 tests)
experiments/
    benchmarks/      # Retrieval/injection quality benchmarks (Round 10)
```

Key design principles:
- **100% local by default** — opt-in anonymous usage statistics (local log in Phase 1; future phases may transmit with re-consent), no cloud
- **User-owned data** — all knowledge stored as human-readable JSON files
- **MCP-native** — every capability exposed as an MCP tool or resource
- **Privacy by default** — trust boundaries, encryption at rest, safe profile filtering

## Development Setup

```bash
git clone https://github.com/Patdolitse/piia-engram.git
cd piia-engram
pip install -e ".[dev]"
```

Requires Python 3.10+. The optional `[secure]` extra adds encryption support, `[remote]` adds SSE transport.

## Running Tests

```bash
python -m pytest tests/ -v
```

Current baseline: **795 tests, 0 failures, 96% coverage** (v3.29.0). All PRs must maintain this.

For retrieval quality benchmarks (requires test data setup):
```bash
python experiments/benchmarks/round10_retrieval_quality/run_round10.py --group t1
```

## Code Guidelines

- **Python 3.10+** — use type hints where they add clarity
- **Keep changes focused** — one concern per PR
- **Readable over clever** — three similar lines beat a premature abstraction
- **Test behavioral changes** — add or update tests when logic changes
- **No external calls in core operations** — piia-engram must never make network requests in core operations. Opt-in anonymous usage statistics (Phase 1: local log only) and `read_web_content` (optional, requires Reader sidecar) are the only exceptions
- **Bilingual content** — user-facing strings should support both Chinese and English

## Security Guidelines

piia-engram handles sensitive personal data. Extra care is required:

- **Never `eval()` or `exec()` user data**
- **HTML output must use `_esc()` for all user-controlled values** (XSS prevention)
- **All identity update methods validate against field whitelists** — don't bypass this
- **Trust boundaries (`restricted_fields`) must be respected** in any new data exposure path
- **Encryption** — sensitive profile fields (email, phone, etc.) are encrypted at rest; don't add fields to `ENCRYPTED_PROFILE_FIELDS` without review
- **Report vulnerabilities privately** — see [SECURITY.md](SECURITY.md)

## Commit Messages

Follow the pattern: `type: short description`

Types: `feat`, `fix`, `security`, `docs`, `test`, `chore`, `refactor`

Examples:
```
feat: add domain-based lesson filtering
fix: prevent XSS in review page domain titles
security: enforce trust boundaries in identity card export
```

## Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes with tests
4. Run the full test suite — all 795 tests must pass
5. Open a PR explaining **what** changed and **why**

PR titles should be under 70 characters. Use the description for details.

## Reporting Issues

Please include:
- Operating system and Python version
- Steps to reproduce
- Expected vs actual behavior
- piia-engram version (`pip show piia-engram`)

**Security vulnerabilities**: Do NOT open a public issue. Email piia-engram-security@proton.me instead.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).

## Code of Conduct

Be respectful, practical, and constructive. The goal is to make AI tools remember people better while keeping that memory under the user's control.
