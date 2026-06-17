# MCP tool surface analysis

This page maps piia-engram's current MCP tool surface and explains how to present it safely. It is analysis only and documentation-only: it does not change the schema, remove tools, rename tools, or publish anything.

Source of truth:

- `src/piia_engram/mcp_server.py`
- `TIER1_TOOLS` for the default core set
- `TOOL_GOVERNANCE_CLASS` for side-effect and governance class
- `tests/snapshots/mcp_tool_schema.json` for the generated schema snapshot

Current count: **55 MCP tools**. The default server loads **17 core tools** with `ENGRAM_TOOLS=core`; the other **38 advanced tools** are available with `ENGRAM_TOOLS=all`. The machine-readable taxonomy snapshot is [`mcp-tool-surface.json`](mcp-tool-surface.json), and `tests/test_mcp_tool_surface_classification.py` verifies that it stays aligned with `scripts/count_mcp_tools.py --json`.

v4.0 consolidated the previous 87-tool surface into 53 tools, and later source-aware freshness work added two owner-only freshness tools (`confirm_knowledge` and `check_anchors`) for the current 55-tool surface. Families of closely related operations were merged into single tools with a `mode`/`action` selector (for example `get_identity_facets`, `manage_playbook`, `review_staging`), and legacy Playbook scope migration moved out of MCP into the owner-only local CLI. See [`migration-v4.md`](migration-v4.md) for the old-name → new-call mapping.

## Core is not read-only

The core tier means "common in daily sessions", not "safe/read-only". Some core tools write memory or create owner-gated export files. Runtime governance still applies. Owner/export and owner/admin tools are runtime-gated even when they appear in a high-frequency tier:

| Governance class | Count | Meaning |
|---|---:|---|
| `read` | 26 | Reads or reports metadata without ordinary store mutation. |
| `governed_write` | 20 | Mutates ordinary knowledge/project/session data through the write gate. |
| `owner_only_write` | 4 | Changes trust grants/imports, or applies owner-only freshness stamps/checks. |
| `export_owner_only` | 5 | Writes full-knowledge exports or local files and requires owner-level export permission. |

`get_identity_card` is intentionally in the 17-tool core set for discoverability, but it is classed as `export_owner_only`: it writes an identity-card export file and can include identity, lessons, and decisions. Treat it as "core but owner-gated", not as a harmless read. Merged tools keep the strictest gate of their family: `playbook_execution` is `governed_write` and its `prepare` action additionally passes the export gate before writing a local execution-plan file, and `review_staging` runs the write gate for every action, including `list`.

## Release posture by bucket

| Posture | Count | Meaning |
|---|---:|---|
| General publishable | 50 | Broad Engram product capability. Many belong in advanced/admin docs rather than first-run docs. |
| Optional local / dogfood in current form | 5 | Useful, but depends on local paths, an optional local Reader, or beta-maintainer workflow. |
| Internal maintenance / legacy | 0 (moved to CLI) | Legacy Playbook scope migration is no longer an MCP surface; it lives in the owner-only local CLI. |

## Tier-1 core tools

Loaded by default:

- `get_user_context`
- `wrap_up_session`
- `memory_store`
- `add_lesson`
- `add_decision`
- `add_playbook`
- `search_knowledge`
- `get_relevant_knowledge`
- `get_recall`
- `get_identity_card`
- `update_identity`
- `get_project_context`
- `save_project_snapshot`
- `get_recent_context`
- `get_daily_log`
- `get_resume_brief`
- `doctor`

## General publishable tools

These are publishable as general Engram capabilities, assuming their existing governance/write gates remain in force.

### Startup, recall, and session continuity

- `get_user_context`
- `get_recall`
- `get_resume_brief`
- `get_recent_context`
- `get_daily_log`
- `save_agent_context`
- `list_agent_sessions`
- `wrap_up_session`
- `refresh_quick_context` (owner-gated export surface)

### Identity, preferences, and governance

- `get_identity_card` (core but owner-gated export surface)
- `get_identity_facets` — `facet`: profile / preferences / trust_boundaries / work_style / quality_standards / domains / all
- `update_identity`
- `user_portrait` — `action`: get / save / compare
- `get_permission_profile`
- `manage_caller_trust` (owner/admin) — `action`: grant / revoke
- `get_audit_log`
- `preview_context_governance` (advanced owner-gated proposal surface)

### Knowledge read and search

- `get_lessons`
- `get_decisions` — plus `thread_seed_id` / `history_question` for decision threads and revision history
- `get_project_context`
- `list_projects`
- `get_relevant_knowledge`
- `get_knowledge_inheritance`
- `search_knowledge`
- `get_knowledge_overview`
- `explore_knowledge` — `mode`: related / similar / merge_candidates
- `get_stale_knowledge`
- `export_knowledge_report` (owner-gated export surface)

### Knowledge write and curation

- `memory_store` — single writes by `kind`, batch writes via `items_json`
- `add_lesson`
- `add_decision`
- `add_playbook`
- `ingest_notes`
- `extract_session_insights`
- `update_knowledge`
- `archive_knowledge`
- `confirm_knowledge` (owner-only confirmation stamp)
- `check_anchors` (owner-only anchor revalidation)
- `review_staging` — `action`: list / batch / review_item / apply_text
- `request_outline_review` (owner-gated local HTML export surface)
- `merge_knowledge`
- `manage_relation` — `action`: link / unlink

### Playbooks and execution

- `get_playbooks` — `mode`: list / get / recent / management
- `manage_playbook` — `action`: update / archive / delete / restore
- `playbook_execution` — `action`: prepare (owner-gated local execution-plan file surface) / update_step / status

### Project, portability, and diagnostics

- `save_project_snapshot`
- `export_engram` (owner-gated export; `format="openclaw"` for OpenClaw-compatible files)
- `import_engram` (owner/admin import; `format="openclaw"` supported)
- `doctor`
- `start_project`

## Optional local / dogfood tools

These can be productized, but should be framed as optional local integrations rather than universal cloud features.

- `register_tool` - local environment registry write; stores local tool path/version/purpose metadata.
- `find_tool` - local environment registry read.
- `list_tools` - local environment registry read.
- `read_web_content` - optional local Reader integration; fetches only a user-provided URL.
- `export_feedback_report` - internal/dogfood beta-maintainer report.

## Internal maintenance / legacy tools

Legacy Playbook scope migration (classify, apply, rollback, review queue, resolve) is no longer exposed as MCP tools. It moved to the owner-only local CLI in v4.0: `engram playbook scope classify|apply|rollback|queue|resolve` (previews by default; writes require `--apply --yes`). The CLI process is owner-by-construction, which is a stronger boundary than runtime caller classification, and it keeps maintenance churn out of the AI-facing tool list. The deprecated `get_work_style` compatibility read was absorbed into `get_identity_facets(facet="work_style")`.

## Consolidation status (v4.0)

The consolidation direction described in earlier revisions of this page shipped in v4.0: 87 tools became 53 with merged `mode`/`action` selectors and no transitional aliases. Old names were removed in the same release rather than coexisting, so the schema snapshot, governance table, and docs all describe a single surface.

Recommended presentation:

- **Core**: the 17 default tools for first-run and daily use.
- **Advanced**: knowledge curation, Playbook management, governance, import/export, and optional local integrations.
- **Owner/internal**: legacy Playbook scope migration (owner CLI), beta-maintainer reports, and high-blast-radius owner/admin operations.

Public docs should describe export/import/file-writing/grant-mutating tools as owner/admin/export surfaces (Owner/export and Owner/admin labels in the schema docstrings) even when the tool is broadly useful.
