# Operator MCP cheatsheet

This is the short operational view of the Engram MCP surface.

## Default surface

- Default: 17 core tools.
- Opt-in full surface: all 53 tools with `ENGRAM_TOOLS=all` (17 core + 36 advanced).
- Core means high-frequency and context-budget friendly. It does not mean read-only.

## Capability modes

`ENGRAM_TOOLS` accepts composable capability modes separated by `+`.
Tokens are case-insensitive, surrounding whitespace is ignored, and `core` is
always included. `all` exposes the full surface even when combined with other
tokens. Unknown tokens are ignored with a bilingual stderr warning; if every
token is unknown, Engram falls back to `core`. `engram doctor` reports the raw
value, resolved modes, retained tool count, and ignored unknown tokens.

For production or sensitive environments, start with `core` and add only the
groups you need. The setup wizard still defaults to `all` unless you choose a
smaller mode or keep an existing hand-edited value.

```text
ENGRAM_TOOLS=core
ENGRAM_TOOLS=core+governance
ENGRAM_TOOLS=knowledge+integrations
ENGRAM_TOOLS=all
```

| Mode | Tools |
|---|---|
| `knowledge` | `refresh_quick_context`, `get_identity_facets`, `get_lessons`, `get_decisions`, `list_projects`, `get_knowledge_inheritance`, `get_knowledge_overview`, `explore_knowledge`, `export_knowledge_report`, `ingest_notes`, `extract_session_insights`, `update_knowledge`, `archive_knowledge`, `review_staging`, `get_stale_knowledge`, `request_outline_review`, `merge_knowledge`, `manage_relation`, `get_playbooks`, `manage_playbook`, `playbook_execution` |
| `governance` | `get_permission_profile`, `manage_caller_trust`, `get_audit_log`, `preview_context_governance` |
| `admin` | `user_portrait`, `export_engram`, `import_engram`, `export_feedback_report`, `start_project`, `save_agent_context`, `list_agent_sessions` |
| `integrations` | `read_web_content`, `register_tool`, `find_tool`, `list_tools` |

## 能力模式

`ENGRAM_TOOLS` 支持用 `+` 组合能力模式。token 大小写不敏感，两侧空白会被忽略，
并且始终隐含 `core`。`all` 即使和其他 token 混用，也会暴露完整工具面。未知
token 会被忽略，并向 stderr 输出中英双语警告；如果所有 token 都未知，则回落到
`core`。`engram doctor` 会报告原始值、解析后的模式、保留工具数，以及被忽略的未知
token。

生产或敏感环境建议从 `core` 起步，再按需加组。setup 向导默认仍写入 `all`，除非你
主动选择更小的模式，或选择保留已有手改值。

| 模式 | 工具 |
|---|---|
| `knowledge` | `refresh_quick_context`, `get_identity_facets`, `get_lessons`, `get_decisions`, `list_projects`, `get_knowledge_inheritance`, `get_knowledge_overview`, `explore_knowledge`, `export_knowledge_report`, `ingest_notes`, `extract_session_insights`, `update_knowledge`, `archive_knowledge`, `review_staging`, `get_stale_knowledge`, `request_outline_review`, `merge_knowledge`, `manage_relation`, `get_playbooks`, `manage_playbook`, `playbook_execution` |
| `governance` | `get_permission_profile`, `manage_caller_trust`, `get_audit_log`, `preview_context_governance` |
| `admin` | `user_portrait`, `export_engram`, `import_engram`, `export_feedback_report`, `start_project`, `save_agent_context`, `list_agent_sessions` |
| `integrations` | `read_web_content`, `register_tool`, `find_tool`, `list_tools` |

## Core boundaries

- Normal startup/read tools: `get_user_context`, `get_resume_brief`,
  `get_recall`, `search_knowledge`, `get_relevant_knowledge`.
- Core write tools: `memory_store`, `add_lesson`, `add_decision`,
  `add_playbook`, `update_identity`, `save_project_snapshot`,
  `wrap_up_session`.
- Core export surface: `get_identity_card` is owner-gated because it writes and
  returns a portable Markdown identity card.

## When to enable all tools

Use `ENGRAM_TOOLS=all` when you intentionally need:

- review queues, merges, and stale-knowledge maintenance;
- imports, exports, and backup previews;
- Playbook maintenance and local tool-registry operations;
- proposal-only context-governance previews;
- owner/admin diagnostics.

All-tool mode increases the visible tool list. It does not remove governance
checks, owner gates, or the need to confirm public actions.

Legacy Playbook scope migration is not an MCP surface: it lives in the
owner-only local CLI (`engram playbook scope classify|apply|rollback|queue|resolve`).

## Evidence labels

- L0/L1: installed, wired, or reachable.
- L2: read/search behavior observed.
- L3: Engram-on beats a control arm with zero-pollution evidence.
- L4: one client writes or exports and another cold-starts and recalls.
- L5: scrubbed, reproducible, public-safe evidence.

Before publishing a claim, check the evidence pack with
`evidence_readiness(...)` and the wording with `validate_public_claim(...)`.
