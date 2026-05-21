# MCP 协调器第二轮验证基准报告

## 1. 运行说明

- 评估代理：rule_based
- 真实 LLM：本轮按任务要求不使用
- 总场景数：50
- 测试 D：30 个第一轮复用场景中的 13 个 remember 场景
- 测试 C：10 个同义改写场景（2 组×5）
- 测试 E：10 个近义工具混淆场景
- 边界：未修改 `mcp_server.py` / `core.py` / `setup_wizard.py` / `experiments/coordinator/high_level_actions.py`

## 2. 测试 D 结果

| 指标 | 关键词推断（第一轮原型） | 显式传参（第二轮） | 差异 |
|------|-------------------------|--------------------|------|
| Easy kind | 100.0% | 100.0% | +0.0pp |
| Medium kind | N/A | N/A | N/A |
| Hard kind | 28.6% | 100.0% | +71.4pp |
| 整体 domain | 15.4% | 100.0% | +84.6pp |

判定结论：通过。
通过门槛：Hard kind ≥ 95%，整体 domain ≥ 90%，Easy/Medium kind ≥ 98%。
样本说明：Easy=6，Medium=0，Hard=7；Medium 为空时表格记为 N/A。

## 3. 测试 C 结果

| 同义改写组 | 关键词模式稳定性 | 显式传参稳定性 |
|-----------|-----------------|----------------|
| decision_pytest | 3/5 (60.0%) | 5/5 (100.0%) |
| lesson_lockfile | 3/5 (60.0%) | 5/5 (100.0%) |

- 关键词模式整体稳定性：60.0%
- 显式传参整体稳定性：100.0%
- 差距：+40.0pp
- 判定结论：通过。

## 4. 测试 E 结果

| 近义工具组 | 37 工具准确率 |
|-----------|--------------|
| lesson_decision_extract | 2/4 (50.0%) |
| search_relevant_similar | 1/3 (33.3%) |
| snapshot_context_user | 1/3 (33.3%) |

- 37 工具总体准确率：4/10 (40.0%)
- 判定结论：协调器有价值。

代表性错例：

- E01：期望 `extract_session_insights`，实际 `add_lesson`；把这段会话里的经验和决策自动提取出来，沉淀到 Engram
- E04：期望 `extract_session_insights`，实际 `search_knowledge`；把下面的会议纪要批量分析出 lessons 和 decisions
- E05：期望 `save_project_snapshot`，实际 `search_knowledge`；保存当前项目快照，包含入口、构建命令和产物路径
- E06：期望 `get_project_context`，实际 `search_knowledge`；读取这个项目的历史项目上下文，不要加载完整用户身份
- E09：期望 `get_relevant_knowledge`，实际 `search_knowledge`；根据当前项目路径给我最相关的跨项目知识

## 5. 综合决策

### Q1：显式传参方案是否能上线？

可以作为下一版协调器的必要条件。
数据：Hard kind 从 28.6% 到 100.0%，domain 从 15.4% 到 100.0%。

### Q2：协调器是否对同义改写更稳定？

显式传参明显更稳定。
数据：关键词稳定性 60.0%，显式传参 100.0%，差距 +40.0pp。

### Q3：37 工具在近义场景下是否真的够用？

测试 E 给出的结论是：协调器有价值。37 工具在近义混淆场景下准确率为 40.0%。

### 最终建议

方案 B：做协调器 + 显式传参，但先进入第三轮真实 LLM 和端到端测试。本轮 D/C 通过，E 显示 37 工具在近义场景下不足。

## 6. 第三轮测试预告

- 真实 LLM：需要 OpenAI/Anthropic API key，每个场景至少 3 次取多数。
- 端到端写入：调用 Engram 后检查 lesson/decision 内容、domain、reasoning 是否符合预期。
- 大样本：扩到 100+ 场景，覆盖更多自然语言、项目上下文和维护动作。
