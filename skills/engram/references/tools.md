# Engram MCP tools — routing reference

This lists **real** Engram MCP tools by intent. Use these names exactly; do not
invent tool names. By default the server loads the Tier-1 core set; the rest
become available when the server is launched with `ENGRAM_TOOLS=all`. Core is a
high-frequency surface, not a read-only guarantee; owner/export/admin and write
tools are still runtime-gated by Engram's governance classes.

## Tier-1 core (loaded by default)

### Read / recall
- `get_resume_brief` — recover the last thread of work to continue a session.
- `get_user_context` — identity, preferences, and standards for a fresh start.
- `get_relevant_knowledge` — let Engram surface knowledge relevant to the moment.
- `search_knowledge` — search prior lessons, decisions, and notes by topic.
- `get_identity_card` — the user's portable identity card (owner-gated export/identity).
- `get_project_context` — context for the current project.
- `get_recent_context` — recent activity across sessions.
- `get_daily_log` — the day's activity log.
- `doctor` — health check of the local Engram store.

### Write (user-approved)
- `add_lesson` — capture a lesson learned / gotcha / validated result.
- `add_decision` — record a decision, its rationale, and what was not chosen.
- `add_playbook` — save a repeatable multi-step procedure.
- `memory_store` — store a piece of context/memory.
- `save_project_snapshot` — snapshot the current project state.
- `wrap_up_session` — checkpoint context at the end of a session.
- `update_identity` — update the user's identity/preferences when they change.

## Tier-2 advanced (requires `ENGRAM_TOOLS=all`)

Available when the operator opts into the full tool set. A non-exhaustive,
intent-grouped sample of real tools:

- **Knowledge management**: `bulk_add_knowledge`, `update_knowledge`,
  `archive_knowledge`, `review_knowledge`, `get_stale_knowledge`,
  `merge_knowledge`, `link_knowledge`, `get_knowledge_overview`,
  `get_knowledge_inheritance`, `find_similar_knowledge`.
- **Playbooks**: `get_playbook`, `update_playbook`, `prepare_playbook_execution`,
  `update_execution_step`, `get_execution_status`, `archive_playbook`.
- **Identity / profile reads**: `get_profile`, `get_preferences`,
  `get_work_style` (deprecated compatibility read; prefer `get_preferences`),
  `get_quality_standards`, `get_trust_boundaries`.
- **Tool graph**: `register_tool` (governed write), `find_tool`, `list_tools`
  for optional local tool/program registry workflows.
- **Export / import**: `export_engram`, `import_engram`, `export_knowledge_report`
  are owner/admin/export surfaces; treat returned files as private.
- **Context governance**: `preview_context_governance` is an advanced,
  owner-gated preview surface for safe-context, freshness/conflict, replay, and
  evidence proposals. It does not apply changes or publish drafts.
- **Optional local integrations**: `read_web_content` expects a local Reader API
  service; it is not required for the default memory workflow.
- **Audit / governance**: `get_audit_log`, `extract_session_insights`.
- **Sessions**: `save_agent_context`, `list_agent_sessions`, `get_recent_context`.

## Local CLI (run in a terminal, not via MCP)

These are real `engram` subcommands the user runs locally. They are read-only or
explicit-output only — they never touch files outside the Engram directory.

- `engram backup-plan` — metadata-only plan of what to copy before an upgrade
  (no stored knowledge bodies are printed).
- `engram export-agents-md [--scope global|project] [--project NAME] [--out PATH]`
  — render the user's **verified, non-sensitive** knowledge as an `AGENTS.md` /
  `CLAUDE.md` block. Staging and sensitive items are excluded by construction; it
  refuses to overwrite an existing file.

## Additive recall/provenance options (backward-compatible)

- `search_knowledge` and `get_relevant_knowledge` accept an optional
  `include_freshness=true` to attach a per-item freshness hint
  (`fresh`/`aging`/`stale`/`unknown`). Default is off — the response is unchanged.
- `add_lesson` / `add_decision` / `add_playbook` accept optional `source_agent`,
  `run_id`, and `last_validated_at` provenance fields. Omitting them changes
  nothing.

If you are unsure whether a tool exists or is loaded, list the server's tools or
fall back to the Tier-1 core set above rather than guessing a name.
