# PIIA Engram v3.8.0 — 知识生命周期 + Domain 多标签 + 工作流快捷工具

自 v3.5.1 以来的完整更新。向后兼容，已有用户 `pip install --upgrade piia-engram` 即可。

## 新功能

### 知识生命周期管理（地基类特性）

知识不只是被存，还会被复习、归档、提醒。

- **`review_knowledge`** — 标记知识已复习，刷新 last_reviewed，追踪 access_count
- **`get_stale_knowledge`** — 列出超过指定天数未复习的知识条目
- **`get_health_report()`** 新增 `items_needing_review` 和 `items_to_archive` 字段
- **`get_user_context()`** 超过 5 条过期知识时自动附 stale_knowledge_warning
- **修复**：AI 注入 context 不再污染 staleness 检测（last_reviewed 只在用户主动 review 时更新）

工具总数：39 → **41**

### Domain 多标签支持

- `_infer_domain()` 返回多个匹配域，逗号分隔（如 `"python,testing"`）
- `get_lessons(domain=...)` 从精确匹配改为包含匹配
- `get_decisions` 和 `add_decision` 新增 `domain` 参数，支持按领域过滤决策

### 工作流快捷工具

- **`wrap_up_session`** — 会话结束一键收尾：自动提取 lessons/decisions + 保存项目快照
- **`start_project`** — 新项目一键启动：继承跨项目经验 + 创建项目档案

### 冷启动增强

- `engram setup` 新增引导填写角色、技术栈、踩坑经验，可选导入 CLAUDE.md / .cursorrules
- `get_user_context` 和 `export_identity_card` 自动包含最近 6-8 条 active decision（此前跳过）
- `engram stats` — 一键查看知识资产增长，支持 `--days` 自定义窗口

### 工具描述优化

重写 8 个易混淆工具的 docstring，每个明确标注"用途"和"注意"。Round 5 测试近义混淆准确率 90% → 100%。

## 验证数据

| 轮次 | 范围 | 结果 |
|------|------|------|
| Round 6 | 39 工具全覆盖基准 | 88 场景，98.9% |
| Round 7 | Domain 多标签 | T1 15/15, T2 19/20 |
| Round 8 | Decision domain | T1 8/8, T2 20/20 |
| Round 9 | 知识生命周期 | T1 10/10, T2 20/20 |

所有新功能均经"验证→评估→通过"流程，真实 LLM 评估（DeepSeek V3），不拍脑袋。

## 升级

```bash
pip install --upgrade piia-engram
```

无需重启 MCP，下次工具冷启动自动生效。

---

**Full Changelog**: https://github.com/Patdolitse/engram/compare/v3.5.1...v3.8.0
