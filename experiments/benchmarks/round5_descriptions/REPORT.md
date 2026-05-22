# Engram Round 5 工具描述优化与快捷工具验证报告

## 1. 运行说明

- LLM：DeepSeek（`deepseek-chat`）
- 温度：0.0
- 每个场景：3 次取多数
- 当前工具数：39（从 `src/piia_engram/mcp_server.py` 实时抽取）
- 总场景数：45
- 总调用次数：135
- 失败调用：0
- Usage：prompt=360414，completion=6321，total=366735 tokens
- 边界：本轮只验证主代码中已经完成的工具描述和快捷工具，不修改 `mcp_server.py`。

## 2. T1 近义混淆结果

| 组 | Round 3 | Round 5 | 差异 | 判定 |
|----|---------|---------|------|------|
| lesson_decision_extract | 75.0% | 100.0% | +25.0pp | pass |
| search_relevant_similar | 100.0% | 100.0% | +0.0pp | pass |
| snapshot_context_user | 100.0% | 100.0% | +0.0pp | pass |
| 总体 | 90.0% | 100.0% | +10.0pp | pass |

## 3. T2 新工具识别结果

| 指标 | 结果 | 判定 |
|------|------|------|
| wrap_up_session 正确率 | 100.0% | pass |
| start_project 正确率 | 100.0% | pass |
| T2 总体正确率 | 100.0% | pass |

| id | expected | actual | 正确 | 说明 |
|----|----------|--------|------|------|
| T2-W01 | wrap_up_session | wrap_up_session | PASS | 用户明确要求会话结束时保存摘要中的经验和决策，并更新项目快照，wrap_up_session 正好同时完成这两件事。 |
| T2-W02 | wrap_up_session | wrap_up_session | PASS | 用户明确要求收尾工作，需要同时提取 lessons/decisions 并保存项目快照，wrap_up_session 正是为此设计的单一工具调用，能一次性完成这两项任务。 |
| T2-W03 | wrap_up_session | wrap_up_session | PASS | 用户要求结束会话并沉淀总结到Engram，同时更新项目title和tech_stack，wrap_up_session正好支持一键收尾：自动提取知识+保存项目快照，且支持project_title和tech_stack参数。 |
| T2-W04 | wrap_up_session | wrap_up_session | PASS | 用户要求一次性保存修复验证的总结、发现和项目快照作为会话收尾，wrap_up_session 正好能同时完成自动提取知识（总结/发现）和保存项目快照两个任务。 |
| T2-W05 | wrap_up_session | wrap_up_session | PASS | 用户明确要求一次调用完成会话收尾和项目档案更新，wrap_up_session 正好同时做这两件事（自动提取知识 + 保存项目快照）。 |
| T2-S01 | start_project | start_project | PASS | 用户要启动新项目并继承过往经验、创建项目档案，start_project 正好一键完成这两件事。 |
| T2-S02 | start_project | start_project | PASS | 用户要求新建项目并同时获取可继承的经验教训和保存项目快照，start_project 工具正好一次性完成这两件事：根据项目描述匹配已有知识并自动创建项目档案。 |
| T2-S03 | start_project | start_project | PASS | 用户要开始一个新项目（web-mvp 信任审计工具），需要一键初始化项目记录并拉取相关历史经验，start_project 正好满足这两个需求。 |
| T2-S04 | start_project | start_project | PASS | 用户需要基于项目描述继承跨项目经验并同时建立项目快照，start_project 正好一次性完成这两件事。 |
| T2-S05 | start_project | start_project | PASS | 用户要求为新项目开档，包括根据描述推荐旧经验、设置项目路径、标题和技术栈，这正是 start_project 工具的用途。 |

## 4. T3 回归结果

- 正确率：14/15（93.3%）
- 回归场景数：1
- 原子工具场景误选快捷工具：1/15

| 类别 | 正确率 |
|------|--------|
| advanced | 4/4（100.0%） |
| cleanup | 2/2（100.0%） |
| inherit | 0/1（0.0%） |
| project | 2/2（100.0%） |
| recall | 2/2（100.0%） |
| remember | 3/3（100.0%） |
| sync | 1/1（100.0%） |

| id | expected | actual | 回归 | 说明 |
|----|----------|--------|------|------|
| T3-S01 | add_lesson | add_lesson | PASS | 用户明确说出一条踩坑经验，适合用 add_lesson 记录这条关于 Windows Python GBK 编码导致 twine 上传失败的教训。 |
| T3-S04 | add_decision | add_decision | PASS | 用户明确做出了一个技术决策（用PostgreSQL存审计事件），适合用add_decision记录关键决策及其推理。 |
| T3-S14 | search_knowledge | search_knowledge | PASS | 用户明确给出了搜索词'鉴权'，search_knowledge 最适合按关键词搜索经验教训和决策。 |
| T3-S18 | get_knowledge_inheritance | start_project | FAIL | 用户开始新电商项目，需要继承跨项目经验并建立项目档案，start_project 一键完成这两件事。 |
| T3-S21 | get_user_context | get_user_context | PASS | 新对话开始时调用此工具获取用户的完整上下文，包括身份、工作方式、质量标准等冷启动信息。 |
| T3-S24 | get_knowledge_overview | get_knowledge_overview | PASS | 用户要求整理重复的经验，但不自动合并。get_knowledge_overview 可以获取知识库的整体概览和健康报告，帮助识别重复条目，而不会自动执行合并操作。 |
| T3-S27 | export_engram | export_engram | PASS | 用户要求导出 Engram 数据为 JSON，export_engram 工具正是用于导出整个 Engram 为单一备份文件（JSON 格式），完全匹配需求。 |
| T3-S28 | merge_knowledge | merge_knowledge | PASS | 用户明确要求手动合并两条 lesson，merge_knowledge 工具正是用于合并两条知识条目，将次要条目的关联转移到主要条目后归档次要条目。 |
| T3-S29 | get_audit_log | get_audit_log | PASS | 用户明确要求查看最近20条审计日志，get_audit_log工具支持按数量返回最近的审计日志条目，设置limit=20即可满足需求。 |
| T3-S30 | update_identity | update_identity | PASS | 用户要求更新 quality_standards 中的验收条件，update_identity 支持更新 quality_standards 字段，可以修改 rules 列表来体现先验证再声称完成的规则。 |
| T3-E01 | extract_session_insights | extract_session_insights | PASS | 用户要求自动从会话摘要中提取经验和决策，不需要手动分类，这正是 extract_session_insights 的用途。 |
| T3-E05 | save_project_snapshot | save_project_snapshot | PASS | 用户明确要求保存当前项目快照，并包含入口、构建命令和产物路径，这正是 save_project_snapshot 工具的用途——写入/更新项目的知识快照，支持 tech_stack、known_issues、notes 等字段来记录这些信息。 |
| T3-E06 | get_project_context | get_project_context | PASS | 用户明确要求读取项目的历史上下文，且不要加载完整用户身份，get_project_context 正是用于读取特定项目知识快照的工具，符合需求。 |
| T3-E09 | get_relevant_knowledge | get_relevant_knowledge | PASS | 用户要求根据当前项目路径获取最相关的跨项目知识，get_relevant_knowledge 正是按项目路径自动推荐最相关的经验教训，无需搜索词，符合需求。 |
| T3-E10 | find_similar_knowledge | find_similar_knowledge | PASS | 用户提供了具体的知识条目ID（lesson-123），想查找相似内容以判断是否需要合并，这正是find_similar_knowledge工具的用途。 |

## 5. 综合判定

**不通过/部分通过**：至少一个测试组未达门槛。
- T3 发现 1 个回归，且原子工具场景误选快捷工具 1/15。
- 回归案例：T3-S18 期望 `get_knowledge_inheritance`，实际 `start_project`；说明新快捷工具可能覆盖了只想继承知识、但不想创建项目快照的场景。

## 6. 与 Round 3 对比总结

- Round 3 Test E 总体：90.0%；Round 5 T1 总体：100.0%（+10.0pp）。
- Round 5 新工具识别：wrap_up_session=100.0%，start_project=100.0%。
- Round 5 T3 选取 15 个核心旧场景，回归数=1。
- `results_raw.jsonl` 合并保存了 T1/T2/T3 的所有原始 request/response。
