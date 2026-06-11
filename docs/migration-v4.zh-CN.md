# 迁移到 v4.0（87 → 53 个 MCP 工具）

[English](migration-v4.md) | [中文](migration-v4.zh-CN.md)

v4.0 将 Engram 的 MCP 工具面从 87 个合并为 53 个。紧密相关的操作族被合并为
带 `mode`/`action` 选择器的单一工具；旧的 Playbook 作用域迁移（legacy scope
migration）移出 MCP，改为 owner 专用的本地 CLI。

**没有过渡别名**：旧名称在同一版本中直接移除。如果 AI 代理调用旧名称，MCP
宿主会报告未知工具——重启会话（或让客户端重新拉取服务器工具列表），然后按
下方的新调用方式使用。

## 没有变化的部分

- **默认加载的 17 个 Tier-1 核心工具**与 v3.x 是同一组。`memory_store` 新增
  了可选的 `items_json` 批量参数；原有的单条写入调用不变。
- **你已存储的数据。** 没有磁盘上的数据迁移；知识、身份、Playbook 和项目文
  件全部原样保留。
- **调用方授权。** 信任授权以 `agent_id` 为键，与工具名无关，已有授权继续
  有效，无需重新批准。
- **治理门。** 每个合并后的工具保留其所属族中最严格的门（详见下方"需要了
  解的行为变化"）。

## 旧名 → 新调用

### 身份读取（6 → 1）

| 旧调用 | 新调用 |
|---|---|
| `get_profile(safe=...)` | `get_identity_facets(facet="profile", safe=...)` |
| `get_preferences()` | `get_identity_facets(facet="preferences")` |
| `get_trust_boundaries()` | `get_identity_facets(facet="trust_boundaries")` |
| `get_work_style()` | `get_identity_facets(facet="work_style")` |
| `get_quality_standards()` | `get_identity_facets(facet="quality_standards")` |
| `get_domains()` | `get_identity_facets(facet="domains")` |

`facet="all"`（默认值）一次调用返回全部六个 facet。

### Playbook 读取（4 → 1，保留 `get_playbooks` 名称）

| 旧调用 | 新调用 |
|---|---|
| `get_playbooks(domain, limit, project_folder)` | 不变（默认 `mode="list"`） |
| `get_playbook(playbook_id, ...)` | `get_playbooks(mode="get", playbook_id=...)` |
| `get_recent_playbooks(limit)` | `get_playbooks(mode="recent", limit=...)` |
| `list_playbooks_for_management(status, scope_type, include_content)` | `get_playbooks(mode="management", status=..., scope_type=..., include_content=...)` |

注意：`mode="recent"` 共享 `get_playbooks` 的 `limit` 默认值 20；旧
`get_recent_playbooks` 默认是 5，如果你依赖旧默认值，请显式传 `limit=5`。
在 `mode="list"` 下传入 `playbook_id` 会隐式升级为 `mode="get"`。

### Playbook 管理（4 → 1）

| 旧调用 | 新调用 |
|---|---|
| `update_playbook(playbook_id, <字段>)` | `manage_playbook(action="update", playbook_id=..., <相同字段>)` |
| `archive_playbook(playbook_id)` | `manage_playbook(action="archive", playbook_id=...)` |
| `delete_playbook(playbook_id, reason, dry_run, confirm)` | `manage_playbook(action="delete", ...)` |
| `restore_playbook(playbook_id, dry_run, confirm)` | `manage_playbook(action="restore", ...)` |

`delete`/`restore` 保留原有安全语义：默认只预览（`dry_run=True`），实际写
入需要 `confirm=True`。

### Playbook 执行（3 → 1）

| 旧调用 | 新调用 |
|---|---|
| `prepare_playbook_execution(playbook_id, params_json, project_folder, confirm_cross_project)` | `playbook_execution(action="prepare", ...)` |
| `update_execution_step(playbook_id, step_order, status, notes)` | `playbook_execution(action="update_step", playbook_id=..., step_order=..., step_status=..., notes=...)` |
| `get_execution_status(playbook_id)` | `playbook_execution(action="status", playbook_id=...)` |

注意：步骤参数 `status` 改名为 `step_status`，以避免与 Playbook 生命周期的
`status` 冲突。

### Staging 评审（4 → 1）

| 旧调用 | 新调用 |
|---|---|
| `list_pending_staging(filters_json, limit, offset)` | `review_staging(action="list", ...)` |
| `batch_review_staging(actions_json, operation, dry_run, confirm)` | `review_staging(action="batch", actions_json=..., dry_run=..., confirm=...)` |
| `review_knowledge(knowledge_id)` | `review_staging(action="review_item", knowledge_id=...)` |
| `apply_review(review_text)` | `review_staging(action="apply_text", review_text=...)` |

### 知识关系（4 → 1）

| 旧调用 | 新调用 |
|---|---|
| `link_knowledge(id_a, id_b)` | `manage_relation(action="link", src_id=..., dst_id=...)` |
| `unlink_knowledge(id_a, id_b)` | `manage_relation(action="unlink", src_id=..., dst_id=...)` |
| `add_relation(src_id, dst_id, rel)` | `manage_relation(action="link", src_id=..., dst_id=..., rel=...)` |
| `remove_relation(src_id, dst_id, rel)` | `manage_relation(action="unlink", src_id=..., dst_id=..., rel=...)` |

`rel` 为空时表示无类型的双向"另见"（see also）链接；`rel` 取值
（`led_to` / `supersedes` / `implemented_by`）时表示有类型、有方向的演化边。

### 知识探索（3 → 1）

| 旧调用 | 新调用 |
|---|---|
| `get_related_knowledge(item_id)` | `explore_knowledge(mode="related", item_id=...)` |
| `find_similar_knowledge(item_id, limit)` | `explore_knowledge(mode="similar", item_id=..., limit=...)` |
| `suggest_merges(threshold, limit)` | `explore_knowledge(mode="merge_candidates", threshold=..., limit=...)` |

`limit=0`（默认值）保持各 mode 原有的默认值（similar：5，
merge_candidates：10）。

### 决策线程（2 → 0，并入 `get_decisions`）

| 旧调用 | 新调用 |
|---|---|
| `get_decision_thread(seed_id)` | `get_decisions(thread_seed_id=...)` |
| `get_decision_history(question, threshold)` | `get_decisions(history_question=..., history_threshold=...)` |

普通的 `get_decisions(...)` 调用行为与之前完全一致。两个新参数同时给出时，
`thread_seed_id` 优先。

### 用户画像（3 → 1）

| 旧调用 | 新调用 |
|---|---|
| `get_user_portrait()` | `user_portrait(action="get")` |
| `save_user_portrait()` | `user_portrait(action="save")` |
| `compare_user_portraits()` | `user_portrait(action="compare")` |

### 导入 / 导出（4 → 2，名称保留）

| 旧调用 | 新调用 |
|---|---|
| `export_engram(...)` | 不变（默认 `format="native"`） |
| `export_engram_to_openclaw(output_dir)` | `export_engram(format="openclaw", output_dir=...)` |
| `import_engram(input_path, merge, dry_run)` | 不变（默认 `format="native"`） |
| `import_engram_from_openclaw(soul_path, memory_path, user_path)` | `import_engram(format="openclaw", soul_path=..., memory_path=..., user_path=...)` |

### 调用方信任（2 → 1）

| 旧调用 | 新调用 |
|---|---|
| `set_caller_trust(agent_id, trust_level)` | `manage_caller_trust(action="grant", agent_id=..., trust_level=...)` |
| `revoke_caller(agent_id)` | `manage_caller_trust(action="revoke", agent_id=...)` |

### 批量知识写入（1 → 0，并入 `memory_store`）

| 旧调用 | 新调用 |
|---|---|
| `bulk_add_knowledge(items_json, item_type)` | `memory_store(kind=<item_type>, items_json=...)` |

批量路径仍会逐条剥离调用方自带的信任字段，基于风险的写入门依然是 tier 的
唯一裁决者。

### 旧 Playbook 作用域迁移（5 → 0，移至本地 CLI）

`classify_legacy_playbooks`、`apply_legacy_playbook_scope_suggestions`、
`rollback_playbook_scope_migration`、`get_playbook_scope_review_queue` 和
`resolve_playbook_scope_review` 不再是 MCP 工具。请改用 owner 专用的本地
CLI：

```
engram playbook scope classify [--project-folders a,b]
engram playbook scope apply    [--playbook-ids ...] [--min-confidence 0.7] [--apply --yes]
engram playbook scope rollback [--playbook-ids ...] [--apply --yes]
engram playbook scope queue    [--include-resolved] [--limit 50]
engram playbook scope resolve <playbook_id> --action accept_global|accept_project|accept_shared|skip [--apply --yes]
```

所有子命令默认只预览；实际落盘需要 `--apply --yes`。

## 需要了解的行为变化

1. **`review_staging` 对包括 `list` 在内的所有 action 都过门。** 旧
   `list_pending_staging` 是只读类工具；合并后的 hub 先过写入门，因此
   read-only-external 调用方现在连 `list` 也会被拒。owner 和 trusted-local
   调用方不受影响。
2. **低信任调用方的拒绝措辞变化。** `user_portrait`（`get` / `compare`）和
   `playbook_execution(action="status")` 对外部调用方现在返回写入门拒绝，
   而不是旧的 owner-only 读取拒绝。结果相同（请求被拒），消息不同。
3. **合并工具取最严门。** `playbook_execution(action="prepare")` 在写本地
   执行计划文件前仍要过 owner 导出门；`get_playbooks(mode="management")`
   保留其 owner-only 结果门。
4. **参数改名。** `update_execution_step` 的 `status` 现为 `step_status`；
   无类型关系对 `id_a`/`id_b` 现为 `src_id`/`dst_id`。
5. **默认值变化。** `get_playbooks(mode="recent")` 使用共享的 `limit` 默认
   值 20（旧 `get_recent_playbooks` 默认是 5）。
6. **增量放宽。** 提供 `items_json` 时 `memory_store` 的 `content_json` 可
   省略；`format="openclaw"` 时 `import_engram` 的 `input_path` 可省略。

## FAQ

**需要迁移已存储的数据吗？** 不需要。本次合并只改 MCP 工具名和签名，磁盘
上的数据不移动、不重写。

**已有的调用方授权还有效吗？** 有效。授权以 `agent_id` 为键，无需重新批
准。审计回执现在显示合并后的工具名。

**我的 AI 工具还在调旧名。** 重启 MCP 服务器/会话，让客户端刷新工具列表。
动态读取工具列表的代理会自动拿到新名称；硬编码在提示词里的旧名请按上方表
格更新。

**当前完整工具清单在哪？** 见
[`tool-surface-analysis.md`](tool-surface-analysis.md)，包含完整的 53 工具
面、治理类别和分层划分。
