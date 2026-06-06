# MCP tool surface analysis

This page maps piia-engram's current MCP tool surface and explains how to present it safely. It is analysis only and documentation-only: it does not change the schema, remove tools, rename tools, or publish anything.

Source of truth:

- `src/piia_engram/mcp_server.py`
- `TIER1_TOOLS` for the default core set
- `TOOL_GOVERNANCE_CLASS` for side-effect and governance class
- `tests/snapshots/mcp_tool_schema.json` for the generated schema snapshot

Current count: **84 MCP tools**. The default server loads **17 core tools** with `ENGRAM_TOOLS=core`; the other **67 advanced tools** are available with `ENGRAM_TOOLS=all`.

## Core is not read-only

The core tier means "common in daily sessions", not "safe/read-only". Some core tools write memory or create owner-gated export files. Runtime governance still applies. Owner/export and owner/admin tools are runtime-gated even when they appear in a high-frequency tier:

| Governance class | Count | Meaning |
|---|---:|---|
| `read` | 42 | Reads or reports metadata without ordinary store mutation. |
| `governed_write` | 28 | Mutates ordinary knowledge/project/session data through the write gate. |
| `owner_only_write` | 7 | Changes trust grants or imports/migrates whole-store state. |
| `export_owner_only` | 7 | Writes full-knowledge exports or local files and requires owner-level export permission. |

`get_identity_card` is intentionally in the 17-tool core set for discoverability, but it is classed as `export_owner_only`: it writes an identity-card export file and can include identity, lessons, and decisions. Treat it as "core but owner-gated", not as a harmless read.

## Release posture by bucket

| Posture | Count | Meaning |
|---|---:|---|
| General publishable | 73 | Broad Engram product capability. Many belong in advanced/admin docs rather than first-run docs. |
| Optional local / dogfood in current form | 5 | Useful, but depends on local paths, an optional local Reader, or beta-maintainer workflow. |
| Internal maintenance / legacy | 6 | Keep available for owner maintenance, but do not present as ordinary public user tools. |

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
- `get_profile`
- `get_preferences`
- `get_trust_boundaries`
- `get_quality_standards`
- `get_domains`
- `update_identity`
- `get_permission_profile`
- `set_caller_trust` (owner/admin)
- `revoke_caller` (owner/admin)
- `get_audit_log`
- `preview_context_governance` (advanced owner-gated proposal surface)

`get_work_style` is not listed here as a primary public capability because it is marked as `Deprecated compatibility read`; prefer `get_preferences`.

### Knowledge read and search

- `get_lessons`
- `get_decisions`
- `get_project_context`
- `list_projects`
- `get_relevant_knowledge`
- `get_knowledge_inheritance`
- `search_knowledge`
- `get_knowledge_overview`
- `suggest_merges`
- `get_related_knowledge`
- `find_similar_knowledge`
- `get_stale_knowledge`
- `get_decision_thread`
- `get_decision_history`
- `export_knowledge_report` (owner-gated export surface)

### Knowledge write and curation

- `memory_store`
- `add_lesson`
- `add_decision`
- `add_playbook`
- `bulk_add_knowledge`
- `ingest_notes`
- `extract_session_insights`
- `update_knowledge`
- `archive_knowledge`
- `review_knowledge`
- `batch_review_staging`
- `list_pending_staging`
- `get_stale_knowledge`
- `request_outline_review` (owner-gated local HTML export surface)
- `apply_review`
- `merge_knowledge`
- `link_knowledge`
- `unlink_knowledge`
- `add_relation`
- `remove_relation`

### Playbooks and execution

- `get_playbooks`
- `get_playbook`
- `get_recent_playbooks`
- `update_playbook`
- `prepare_playbook_execution` (owner-gated local execution-plan file surface)
- `update_execution_step`
- `get_execution_status`
- `archive_playbook`
- `list_playbooks_for_management`
- `delete_playbook`
- `restore_playbook`

### Project, portability, and diagnostics

- `save_project_snapshot`
- `export_engram` (owner-gated export)
- `import_engram` (owner/admin import)
- `export_engram_to_openclaw` (owner-gated export)
- `import_engram_from_openclaw` (owner/admin import)
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

These tools exist for migration and owner maintenance. They should not be emphasized as normal public user tools.

- `get_work_style` - deprecated compatibility read; use `get_preferences`.
- `classify_legacy_playbooks`
- `apply_legacy_playbook_scope_suggestions`
- `rollback_playbook_scope_migration`
- `get_playbook_scope_review_queue`
- `resolve_playbook_scope_review`

## Consolidation direction

Do not remove or rename tools abruptly. The short-term surface reduction already exists through `ENGRAM_TOOLS=core`. Adding consolidated aliases such as `get_identity_state(...)`, `manage_playbook_execution(...)`, or `export_engram(format=...)` would increase the tool count before it decreases it, so those should be future compatibility-planned changes rather than this pass.

Recommended presentation:

- **Core**: the 17 default tools for first-run and daily use.
- **Advanced**: knowledge curation, Playbook management, governance, import/export, and optional local integrations.
- **Owner/internal**: legacy Playbook scope migration, beta-maintainer reports, and high-blast-radius owner/admin operations.

Public docs should describe export/import/file-writing/grant-mutating tools as owner/admin/export surfaces even when the tool is broadly useful.
