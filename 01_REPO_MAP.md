# Repository Map — engram

## What It Does

**engram** is a local-first AI identity layer for MCP-compatible coding tools (Claude Code, Codex, Cursor, Claude Desktop). It stores who the *user* is — profile, preferences, quality standards, lessons learned, key decisions — as local JSON files under `~/.engram/`, exposed via 43 MCP tools so every AI tool reads the same identity. One write, every AI reads.

**Not**: session/agent memory (Mem0, Zep, Letta), an agent framework, or a hosted database.

---

## File Layout

```
engram/
├── README.md / README.zh-CN.md      # Bilingual product README (EN + 中文)
├── LICENSE                          # Apache 2.0
├── NOTICE                           # Standard Apache notice
├── SECURITY.md                      # Security policy + design (bilingual)
├── CONTRIBUTING.md                  # Dev setup, architecture, commit convention
├── CONTRIBUTING.zh-CN.md           # 中文 version
├── CHANGELOG.md                    # Full changelog
├── pyproject.toml                   # Package metadata; name=piia-engram, version=3.16.1
├── install.sh / install.ps1        # Unofficial install helpers
├── assets/                          # Social preview image
├── demos/                           # Demo content
├── docs/
│   ├── architecture.md              # Architecture decision record (post-v3.14.1 refactor)
│   ├── comparison.md                # Side-by-side vs competitors
│   ├── coverage_baseline_v3.14.2.md
│   ├── coverage_baseline_v3.16.1.md
│   ├── design/
│   ├── messaging.md
│   ├── milestone_review_v3.13.2.html
│   ├── milestone_review_v3.13.2.md
│   ├── milestone_review_v3.14.3.md
│   ├── telemetry_roadmap.md
│   └── vision.md
├── experiments/
│   ├── benchmarks/                  # Retrieval quality benchmarks (Round 10)
│   ├── evaluations/                 # v3.14.3 and v3.16.0 evaluation results
│   │   ├── run_evaluation.py
│   │   ├── evidence_pack.md
│   │   └── *.json / *.jsonl
│   └── router/                     # Router experiment
│       ├── router_tool.py
│       └── test_router_tool.py
├── src/engram_core/                # Main package
│   ├── __init__.py
│   ├── core.py                     # Engram class: knowledge CRUD, identity, links
│   ├── mcp_server.py               # MCP server (FastMCP); 43 tools exposed
│   ├── storage.py                  # File I/O, atomic writes, constants, path helpers
│   ├── retrieval.py                # RetrievalMixin: search, ranking, tier promotion
│   ├── context.py                  # ContextMixin: cold-start context, ingestion
│   ├── reconcile.py                # ReconcileMixin: cross-tool sync, staging, conflicts
│   ├── reports.py                  # ReportsMixin: thin hub for 4 report sub-mixins
│   ├── reports_identity.py         # Identity report generation
│   ├── reports_analytics.py        # Analytics/digest reports
│   ├── reports_rarity.py           # Rarity/importance reports
│   ├── reports_review.py           # Review page (interactive HTML)
│   ├── setup_wizard.py             # CLI: `engram setup`, `engram doctor [--fix]`
│   ├── crypto.py                   # AES-256-GCM encryption for sensitive fields
│   ├── telemetry.py               # Opt-in anonymous usage statistics (Phase 1: local log)
│   ├── audit.py                    # AuditLogger: read/write audit trail
│   ├── stats.py                   # CLI: `engram stats [--log]` for GitHub + PyPI metrics
│   └── compat.py                   # OpenClaw import/export helpers
├── tests/
│   ├── test_core.py                # 188 tests — core engine
│   ├── test_reconcile.py           # 58 tests — sync, staging, conflicts
│   ├── test_mcp_coverage.py         # 53 tests — MCP wrapper coverage
│   ├── test_setup_wizard.py         # 50 tests — setup wizard, doctor, telemetry CLI
│   ├── test_mcp_tools.py            # 37 tests — MCP tool wrappers
│   ├── test_telemetry.py            # 30 tests — anonymous usage statistics
│   ├── test_crypto.py               # 27 tests — AES-256-GCM encryption
│   ├── test_packaging.py            # 22 tests — package metadata, CI, tool verification
│   ├── test_stats.py                # 11 tests — GitHub/PyPI statistics
│   ├── test_review_page_xss.py      # 10 tests — XSS prevention in review page
│   ├── test_storage.py              # Storage/file I/O tests
│   └── test_audit.py                # 4 tests — audit logging
└── .github/
    ├── FUNDING.yml
    ├── ISSUE_TEMPLATE/
    ├── pull_request_template.md
    └── workflows/
        ├── ci.yml                  # Ubuntu (3.10–3.13) + macOS + Windows (3.12)
        └── publish.yml             # PyPI publish workflow
```

---

## Core Architecture

**Engram** inherits from 4 mixins (multiple inheritance, Method Resolution Order bottom-to-top):

```
Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)
```

| Mixin | Responsibility |
|---|---|
| `RetrievalMixin` | Weighted search, ranking, similarity detection, tier promotion |
| `ContextMixin` | Cold-start context generation, note ingestion, session insight extraction |
| `ReconcileMixin` | Cross-tool config sync, staging knowledge, conflict detection |
| `ReportsMixin` | Identity/analytics/rarity/review report generation |

**Data storage**: All JSON files under `~/.engram/` (configurable via `ENGRAM_DIR`):
- `identity/profile.json`, `preferences.json`, `quality_standards.json`, `trust_boundaries.json`
- `knowledge/lessons.json`, `decisions.json`, `domains.json`
- `projects/{project_id}.json`
- `exports/`, `compat/openclaw/`

**Encryption** (opt-in): AES-256-GCM with PBKDF2-SHA256 (600k iterations). Encrypted fields stored as `enc:v1:...`. Activate with `pip install piia-engram[secure]` + `ENGRAM_SECRET`.

---

## MCP Tools (43 total)

### Tier-1 Core (10 — default, loaded without env vars)
`get_user_context`, `wrap_up_session`, `add_lesson`, `add_decision`, `search_knowledge`, `get_relevant_knowledge`, `get_identity_card`, `update_identity`, `get_project_context`, `save_project_snapshot`

### Tier-2 Advanced (33 — opt-in via `ENGRAM_TOOLS=all`)
`get_profile`, `get_work_style`, `get_preferences`, `get_trust_boundaries`, `get_quality_standards`, `get_lessons`, `get_decisions`, `get_domains`, `list_projects`, `get_knowledge_inheritance`, `extract_session_insights`, `bulk_add_knowledge`, `ingest_notes`, `update_knowledge`, `archive_knowledge`, `review_knowledge`, `merge_knowledge`, `link_knowledge`, `unlink_knowledge`, `get_knowledge_overview`, `get_related_knowledge`, `find_similar_knowledge`, `get_stale_knowledge`, `export_knowledge_report`, `request_outline_review`, `apply_review`, `export_engram`, `import_engram`, `export_engram_to_openclaw`, `import_engram_from_openclaw`, `read_web_content`, `get_audit_log`, `start_project`

---

## Key Dependencies

| Dependency | Purpose | Required |
|---|---|---|
| `mcp>=1.0` | MCP protocol | Yes |
| `portalocker>=2.0` | Atomic file locking | Yes |
| `cryptography>=41.0` | AES-256-GCM encryption | No (opt-in `[secure]`) |
| `uvicorn>=0.20` | SSE transport for remote mode | No (opt-in `[remote]`) |
| `pytest>=7.0` | Testing | No (opt-in `[dev]`) |

---

## CLI Commands

| Command | Description |
|---|---|
| `engram setup` | Interactive install wizard |
| `engram doctor` | Check MCP config health |
| `engram doctor --fix` | Auto-repair MCP config issues |
| `engram stats` | Show GitHub + PyPI growth metrics |
| `engram stats --log` | Append stats snapshot to local log |
| `engram telemetry` | Manage anonymous usage statistics |
| `engram privacy` | Show what data Engram stores and where |

---

## Development

```bash
pip install -e ".[dev]"      # dev dependencies
python -m pytest tests/ -v   # run all tests
```

---

## Design Principles

- **100% local by default** — no network calls in core; opt-in telemetry only
- **User-owned data** — human-readable JSON files, no proprietary format
- **MCP-native** — every capability exposed as MCP tool/resource
- **Privacy by default** — trust boundaries, field encryption at rest, safe profile filtering
- **No eval/exec** — XSS prevention via `_esc()` in HTML output
- **Bilingual** — user-facing strings support Chinese and English

---

## Known Limitations

- File safety: atomic JSON writes with portalocker; network filesystem edge cases not guaranteed
- Access control: `restricted_fields` is not encryption; any process with read access can read data
- Concurrent writes: protected by file lock + atomic replace, but not for network filesystems
- Caller identity: MCP spec doesn't pass tool identity, so per-caller ACL is blocked
