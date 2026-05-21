# Engram v3.7.0 — 工具描述优化 + 工作流快捷工具

向后兼容。已有用户升级后 AI 工具选择更精准，无需任何配置。

## 新功能

**1. 2 个工作流快捷工具** — 减少重复调用，一次搞定高频操作。

- `wrap_up_session`：会话结束一键收尾，自动提取 lessons/decisions + 保存项目快照。
- `start_project`：新项目一键启动，继承跨项目经验 + 创建项目档案。

快捷工具不替代原子工具——只是把常见的 2-3 步操作合成一步。原有 37 个工具全部保留。

**2. 工具描述优化（8 个工具）** — 重写 3 组易混淆工具的 docstring，每个工具明确标注"用途"和"注意"。

优化的工具组：
- `add_lesson` / `add_decision` / `extract_session_insights`
- `search_knowledge` / `get_relevant_knowledge` / `find_similar_knowledge`
- `save_project_snapshot` / `get_project_context`

## 验证数据（Round 5 基准测试）

DeepSeek V3，45 场景 × 3 次取多数：

| 测试项 | 结果 |
|--------|------|
| 近义混淆准确率 | 90% → **100%**（+10pp） |
| 新工具识别准确率 | **100%** |
| 全量回归 | 14/15（93.3%，1 个可接受边界 case） |

关键改进：Round 3 中最弱的 `add_lesson/add_decision/extract_session_insights` 混淆组从 75% 提升到 100%。

## 工具总数

37 → **39**（新增 2 个快捷工具，原有工具全部保留）。

## 升级

```bash
pip install --upgrade piia-engram
```

无需重启 MCP，下次工具冷启动自动生效。

---

**Full Changelog**: https://github.com/Patdolitse/engram/compare/v3.6.0...v3.7.0

🤖 工具描述由 5 个 AI 模型（Claude/GPT/Gemini/DeepSeek/Cursor）的联合分析驱动，5 轮基准测试验证。
