# MCP tool surface analysis

This is an analysis-only map of piia-engram's MCP tool surface. The supported way to reduce the visible surface already exists: the server loads **17 core tools** by default and exposes the other **66 advanced tools** only when the operator opts into `ENGRAM_TOOLS=all`.

The source of truth is `src/piia_engram/mcp_server.py`: `TIER1_TOOLS` defines
the default set, and `TOOL_GOVERNANCE_CLASS` defines the orthogonal governance
class for each tool.

## Two views of the same surface

Functional clusters answer "what job does this tool do?" Governance classes
answer "what kind of side effect can this tool have?"

Current governance split:

| Class | Count | Meaning |
|---|---:|---|
| `read` | 41 | Reads or reports metadata without store mutation |
| `governed_write` | 28 | Mutates ordinary knowledge/project/session data through the write gate |
| `owner_only_write` | 7 | Changes trust grants or imports/migrates whole-store state |
| `export_owner_only` | 7 | Writes full-knowledge exports or files and requires owner-level export permission |

## Core tools loaded by default

These 17 tools are available under the default `ENGRAM_TOOLS=core` surface:

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

## Functional clusters

### Session lifecycle and continuity

- Core: `get_user_context`, `get_recall`, `get_resume_brief`,
  `get_recent_context`, `get_daily_log`, `wrap_up_session`
- Advanced: `save_agent_context`, `list_agent_sessions`

### Knowledge write and ingestion

- Core: `memory_store`, `add_lesson`, `add_decision`, `add_playbook`
- Advanced: `bulk_add_knowledge`, `ingest_notes`, `extract_session_insights`

### Knowledge read and retrieval

- Core: `search_knowledge`, `get_relevant_knowledge`
- Advanced: `get_lessons`, `get_decisions`, `get_domains`,
  `get_knowledge_inheritance`, `get_knowledge_overview`, `get_stale_knowledge`,
  `get_related_knowledge`, `find_similar_knowledge`, `get_decision_thread`,
  `get_decision_history`

### Knowledge curation and lifecycle

- Advanced: `suggest_merges`, `update_knowledge`, `archive_knowledge`,
  `review_knowledge`, `batch_review_staging`, `list_pending_staging`,
  `request_outline_review`, `apply_review`, `merge_knowledge`,
  `link_knowledge`, `unlink_knowledge`, `add_relation`, `remove_relation`

### Identity and profile

- Core: `get_identity_card`, `update_identity`
- Advanced: `get_profile`, `get_work_style`, `get_preferences`,
  `get_trust_boundaries`, `get_quality_standards`

### Project context

- Core: `get_project_context`, `save_project_snapshot`
- Advanced: `start_project`, `list_projects`

### Playbooks and execution

- Core: none beyond `add_playbook`
- Advanced: `get_playbooks`, `get_playbook`, `get_recent_playbooks`,
  `update_playbook`, `prepare_playbook_execution`, `update_execution_step`,
  `get_execution_status`, `archive_playbook`, `list_playbooks_for_management`,
  `delete_playbook`, `restore_playbook`

### Legacy playbook scope maintenance

- Advanced: `classify_legacy_playbooks`,
  `apply_legacy_playbook_scope_suggestions`,
  `rollback_playbook_scope_migration`, `get_playbook_scope_review_queue`,
  `resolve_playbook_scope_review`

### Local tool registry

- Advanced: `register_tool`, `find_tool`, `list_tools`

### Governance, trust, and audit

- Advanced: `get_permission_profile`, `set_caller_trust`, `revoke_caller`,
  `get_audit_log`

### Portability and import/export

- Advanced: `refresh_quick_context`, `export_knowledge_report`,
  `export_engram`, `import_engram`, `export_engram_to_openclaw`,
  `import_engram_from_openclaw`, `export_feedback_report`

### Optional web read

- Advanced: `read_web_content`

## Observations

- The default tool surface is intentionally small: 17 core tools cover startup,
  read/write, project context, session wrap-up, and diagnostics.
- The advanced set is mostly management surface: review, import/export,
  playbook operations, governance, migration, and reporting.
- The governance view is separate from the functional view. For example,
  `get_identity_card` sounds like a read, but it is classed as
  `export_owner_only` because it writes an export file and can include useful
  identity summaries.
- The playbook-scope migration tools are maintenance-oriented and should rarely
  be needed in normal daily use.

## Non-goals

This document is analysis only, not a deprecation plan. It does not propose merging, renaming, hiding, or removing any tool. It is a map for users and reviewers who want to understand why the default tool set stays small while the full maintenance surface remains available to operators who opt in.
