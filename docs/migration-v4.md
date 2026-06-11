# Migrating to v4.0 (87 → 53 MCP tools)

[English](migration-v4.md) | [中文](migration-v4.zh-CN.md)

v4.0 consolidates Engram's MCP tool surface from 87 tools to 53. Families of
closely related operations were merged into single tools with a `mode`/`action`
selector, and legacy Playbook scope migration moved out of MCP into the
owner-only local CLI.

There are **no transitional aliases**: the old names were removed in the same
release. If an AI agent calls an old name, the MCP host reports an unknown
tool — restart the session (or have the client re-list the server's tools) and
use the new call shapes below.

## What did not change

- **The 17 Tier-1 core tools** loaded by default are the same set as v3.x.
  `memory_store` gained an optional `items_json` batch parameter; its existing
  single-write calls are unchanged.
- **Your stored data.** There is no on-disk migration; knowledge, identity,
  Playbooks, and project files are untouched.
- **Caller grants.** Trust grants are keyed by `agent_id`, not by tool name,
  so existing grants keep working without re-approval.
- **Governance gates.** Each merged tool keeps the strictest gate of its
  family (details under "Behavior changes" below).

## Old name → new call

### Identity reads (6 → 1)

| Old call | New call |
|---|---|
| `get_profile(safe=...)` | `get_identity_facets(facet="profile", safe=...)` |
| `get_preferences()` | `get_identity_facets(facet="preferences")` |
| `get_trust_boundaries()` | `get_identity_facets(facet="trust_boundaries")` |
| `get_work_style()` | `get_identity_facets(facet="work_style")` |
| `get_quality_standards()` | `get_identity_facets(facet="quality_standards")` |
| `get_domains()` | `get_identity_facets(facet="domains")` |

`facet="all"` (the default) returns all six facets in one call.

### Playbook reads (4 → 1, name `get_playbooks` kept)

| Old call | New call |
|---|---|
| `get_playbooks(domain, limit, project_folder)` | unchanged (default `mode="list"`) |
| `get_playbook(playbook_id, ...)` | `get_playbooks(mode="get", playbook_id=...)` |
| `get_recent_playbooks(limit)` | `get_playbooks(mode="recent", limit=...)` |
| `list_playbooks_for_management(status, scope_type, include_content)` | `get_playbooks(mode="management", status=..., scope_type=..., include_content=...)` |

Note: `mode="recent"` shares `get_playbooks`' `limit` default of 20; the old
`get_recent_playbooks` defaulted to 5, so pass `limit=5` explicitly if you
relied on that. Passing `playbook_id` with `mode="list"` implicitly upgrades
the call to `mode="get"`.

### Playbook management (4 → 1)

| Old call | New call |
|---|---|
| `update_playbook(playbook_id, <fields>)` | `manage_playbook(action="update", playbook_id=..., <same fields>)` |
| `archive_playbook(playbook_id)` | `manage_playbook(action="archive", playbook_id=...)` |
| `delete_playbook(playbook_id, reason, dry_run, confirm)` | `manage_playbook(action="delete", ...)` |
| `restore_playbook(playbook_id, dry_run, confirm)` | `manage_playbook(action="restore", ...)` |

`delete`/`restore` keep their safety semantics: preview by default
(`dry_run=True`), writes require `confirm=True`.

### Playbook execution (3 → 1)

| Old call | New call |
|---|---|
| `prepare_playbook_execution(playbook_id, params_json, project_folder, confirm_cross_project)` | `playbook_execution(action="prepare", ...)` |
| `update_execution_step(playbook_id, step_order, status, notes)` | `playbook_execution(action="update_step", playbook_id=..., step_order=..., step_status=..., notes=...)` |
| `get_execution_status(playbook_id)` | `playbook_execution(action="status", playbook_id=...)` |

Note: the step parameter `status` was renamed to `step_status` to avoid
clashing with Playbook lifecycle `status`.

### Staging review (4 → 1)

| Old call | New call |
|---|---|
| `list_pending_staging(filters_json, limit, offset)` | `review_staging(action="list", ...)` |
| `batch_review_staging(actions_json, operation, dry_run, confirm)` | `review_staging(action="batch", actions_json=..., dry_run=..., confirm=...)` |
| `review_knowledge(knowledge_id)` | `review_staging(action="review_item", knowledge_id=...)` |
| `apply_review(review_text)` | `review_staging(action="apply_text", review_text=...)` |

### Knowledge relations (4 → 1)

| Old call | New call |
|---|---|
| `link_knowledge(id_a, id_b)` | `manage_relation(action="link", src_id=..., dst_id=...)` |
| `unlink_knowledge(id_a, id_b)` | `manage_relation(action="unlink", src_id=..., dst_id=...)` |
| `add_relation(src_id, dst_id, rel)` | `manage_relation(action="link", src_id=..., dst_id=..., rel=...)` |
| `remove_relation(src_id, dst_id, rel)` | `manage_relation(action="unlink", src_id=..., dst_id=..., rel=...)` |

With `rel` empty the relation is the untyped bidirectional "see also" link;
with `rel` set (`led_to` / `supersedes` / `implemented_by`) it is the typed,
directed evolution edge.

### Knowledge exploration (3 → 1)

| Old call | New call |
|---|---|
| `get_related_knowledge(item_id)` | `explore_knowledge(mode="related", item_id=...)` |
| `find_similar_knowledge(item_id, limit)` | `explore_knowledge(mode="similar", item_id=..., limit=...)` |
| `suggest_merges(threshold, limit)` | `explore_knowledge(mode="merge_candidates", threshold=..., limit=...)` |

`limit=0` (the default) keeps each mode's previous default (similar: 5,
merge_candidates: 10).

### Decision threads (2 → 0, absorbed into `get_decisions`)

| Old call | New call |
|---|---|
| `get_decision_thread(seed_id)` | `get_decisions(thread_seed_id=...)` |
| `get_decision_history(question, threshold)` | `get_decisions(history_question=..., history_threshold=...)` |

Plain `get_decisions(...)` calls behave exactly as before. If both new
parameters are given, `thread_seed_id` wins.

### User portrait (3 → 1)

| Old call | New call |
|---|---|
| `get_user_portrait()` | `user_portrait(action="get")` |
| `save_user_portrait()` | `user_portrait(action="save")` |
| `compare_user_portraits()` | `user_portrait(action="compare")` |

### Import / export (4 → 2, names kept)

| Old call | New call |
|---|---|
| `export_engram(...)` | unchanged (default `format="native"`) |
| `export_engram_to_openclaw(output_dir)` | `export_engram(format="openclaw", output_dir=...)` |
| `import_engram(input_path, merge, dry_run)` | unchanged (default `format="native"`) |
| `import_engram_from_openclaw(soul_path, memory_path, user_path)` | `import_engram(format="openclaw", soul_path=..., memory_path=..., user_path=...)` |

### Caller trust (2 → 1)

| Old call | New call |
|---|---|
| `set_caller_trust(agent_id, trust_level)` | `manage_caller_trust(action="grant", agent_id=..., trust_level=...)` |
| `revoke_caller(agent_id)` | `manage_caller_trust(action="revoke", agent_id=...)` |

### Batch knowledge writes (1 → 0, absorbed into `memory_store`)

| Old call | New call |
|---|---|
| `bulk_add_knowledge(items_json, item_type)` | `memory_store(kind=<item_type>, items_json=...)` |

The batch path still strips caller-supplied trust fields per item, so the
risk-based write gate remains the sole authority over tiers.

### Legacy Playbook scope migration (5 → 0, moved to the local CLI)

`classify_legacy_playbooks`, `apply_legacy_playbook_scope_suggestions`,
`rollback_playbook_scope_migration`, `get_playbook_scope_review_queue`, and
`resolve_playbook_scope_review` are no longer MCP tools. Run the owner-only
local CLI instead:

```
engram playbook scope classify [--project-folders a,b]
engram playbook scope apply    [--playbook-ids ...] [--min-confidence 0.7] [--apply --yes]
engram playbook scope rollback [--playbook-ids ...] [--apply --yes]
engram playbook scope queue    [--include-resolved] [--limit 50]
engram playbook scope resolve <playbook_id> --action accept_global|accept_project|accept_shared|skip [--apply --yes]
```

All subcommands preview by default; writes require `--apply --yes`.

## Behavior changes to know about

1. **`review_staging` gates every action, including `list`.** The old
   `list_pending_staging` was a read-class tool; the merged hub runs the write
   gate first, so a read-only-external caller is now refused even for `list`.
   Owner and trusted-local callers see no change.
2. **Refusal wording for low-trust callers.** `user_portrait` (`get` /
   `compare`) and `playbook_execution(action="status")` now return a
   write-gate refusal for external callers instead of the old owner-only read
   refusal. Same outcome (request denied), different message.
3. **Strictest-gate-wins on merged tools.**
   `playbook_execution(action="prepare")` still passes the owner export gate
   before writing a local execution-plan file, and
   `get_playbooks(mode="management")` keeps its owner-only result gate.
4. **Parameter renames.** `update_execution_step`'s `status` is now
   `step_status`; the untyped relation pair `id_a`/`id_b` is now
   `src_id`/`dst_id`.
5. **Default change.** `get_playbooks(mode="recent")` uses the shared `limit`
   default of 20 (old `get_recent_playbooks` default was 5).
6. **Additive loosening.** `memory_store`'s `content_json` is optional when
   `items_json` is given; `import_engram`'s `input_path` is optional when
   `format="openclaw"`.

## FAQ

**Do I need to migrate stored data?** No. The consolidation only changes the
MCP tool names and signatures; nothing on disk moves or is rewritten.

**Do my caller grants still work?** Yes. Grants are keyed by `agent_id`, so
nothing needs re-approval. Audit receipts now show the merged tool names.

**My AI tool still tries old names.** Restart the MCP server/session so the
client refreshes its tool list. Agents that read tool lists dynamically pick
up the new names automatically; hard-coded prompts should be updated with the
tables above.

**Where is the current tool inventory?** See
[`tool-surface-analysis.md`](tool-surface-analysis.md) for the full 53-tool
surface, governance classes, and tier split.
