# Contributing to piia-engram

Thanks for considering a contribution to piia-engram — the AI identity layer that stores who you are, not just what you did.

## Documentation language

**English is the canonical language for all repository docs** (README, CHANGELOG, CONTRIBUTING, SECURITY, `docs/`). Write and update docs in English; do not mix languages within a file. The only translated doc is the README (`README.zh-CN.md`) as a courtesy front door for Chinese readers — keep it in sync when you change the English README. Everything else stays English-only to avoid translation drift (the same approach as uv, vue, and most MCP-ecosystem projects). If broad Chinese documentation is needed later, it will live in a docs site with per-locale folders + a translation platform, not hand-maintained file pairs.

Note: this is about *repository documentation*. Product/app user-facing strings (CLI output, setup wizard) remain bilingual — see "Code Guidelines".

## Architecture Overview

```
src/piia_engram/
    core.py            # Core engine: knowledge CRUD, identity, link management
    retrieval.py       # RetrievalMixin — search, ranking, tier promotion
    context.py         # ContextMixin — cold-start context, ingestion helpers
    reconcile.py       # ReconcileMixin — cross-tool memory/config sync
    reports.py         # ReportsMixin — thin hub composing 4 sub-mixins
    mcp_server.py      # MCP tool/resource definitions (the AI-facing API)
    setup_wizard.py    # Interactive setup CLI + doctor diagnostics + instruction injection
    hooks/             # Claude Code lifecycle hooks (Stop/PreCompact/PostCompact/SessionStart)
    crypto.py          # AES-256-GCM encryption for sensitive profile fields
    telemetry.py       # Opt-in anonymous usage statistics (local log first; remote/feedback are separate opt-ins)
tests/                 # 2700+ tests across all modules
experiments/
    benchmarks/      # Retrieval/injection quality benchmarks
```

Key design principles:
- **100% local by default** — identity and knowledge tools are local; telemetry/feedback remain separate explicit opt-ins
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

Current baseline: **2700+ tests passing, 0 failures**. All PRs must maintain a passing suite.

For retrieval quality benchmarks (requires test data setup):
```bash
python experiments/benchmarks/run_benchmarks.py
```

## AI-Assisted Review Loop

Maintainer-led AI work should use a two-agent loop when both tools are available:

1. **Codex implements first**: inspect the repo, make the scoped change, add or update tests, run local verification, and prepare the release or review evidence.
2. **Claude independently reviews**: run Claude Code from a written task package, with review/test/report permissions only by default. Claude should not push, tag, release, merge, or make public GitHub comments without maintainer approval.
3. **Codex reconciles the result**: read Claude's report, classify findings, apply any required fixes, rerun verification, and update release evidence only when the independent review explicitly passes.
4. **Remote actions stay gated**: GitHub push, release, merge, tag, registry publish, PyPI publish, and public issue/PR replies require explicit maintainer confirmation even when both AI agents agree.

Security-sensitive, release, encoding, privacy, governance, permission, encryption, telemetry, and persistence changes should keep the Claude review report in `release-evidence/` or the project audit results folder and reference it from the release evidence file.

## Code Guidelines

- **Python 3.10+** — use type hints where they add clarity
- **Keep changes focused** — one concern per PR
- **Readable over clever** — three similar lines beat a premature abstraction
- **Test behavioral changes** — add or update tests when logic changes
- **No external calls in core operations** — piia-engram must never make network requests in core identity, knowledge, search, review, or governance operations. Local telemetry logging makes no network requests; remote telemetry and feedback reports require separate explicit opt-ins and send metadata-only counts. `read_web_content` is optional and makes outbound HTTP only when explicitly invoked for a URL
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
4. Run the full test suite — all tests must pass
5. Open a PR explaining **what** changed and **why**

PR titles should be under 70 characters. Use the description for details.

## Reporting Issues

Please include:
- Operating system and Python version
- Steps to reproduce
- Expected vs actual behavior
- piia-engram version (`pip show piia-engram`)

Automated promotional issues, repeated grading notifications, and unsolicited badge-placement requests are not accepted. piia-engram's README is reserved for official project documentation, verified ecosystem listings, and installation guidance.

**Security vulnerabilities**: Do NOT open a public issue. Email engram-security@proton.me instead.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).

## Code of Conduct

Be respectful, practical, and constructive. The goal is to make AI tools remember people better while keeping that memory under the user's control.
