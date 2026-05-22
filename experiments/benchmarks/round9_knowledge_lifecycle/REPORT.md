# Engram Round 9：知识生命周期验证报告

## 1. 运行说明

- 范围：验证知识生命周期改动；未修改主代码。
- T1：Python 直接测试 `Engram(root=tempdir)`，不用 LLM，不污染用户数据。
- T2：DeepSeek `deepseek-chat`，从 Round 6 抽取 20 个核心场景，每场景 3 次取多数。
- T2 工具数：41（从 `src/piia_engram/mcp_server.py` 实时抽取，应为 41）。
- T2 调用次数：60，失败调用：0。
- T2 usage：prompt=182877，completion=3225，total=186102 tokens。

## 2. T1 单元测试结果

- 总体：10/10，pass

| 分组 | 结果 | 判定 |
|------|------|------|
| review_knowledge | 3/3 | pass |
| get_stale_knowledge | 3/3 | pass |
| health_report | 2/2 | pass |
| user_context_warning | 2/2 | pass |

| id | category | input | expected | actual | 正确 |
|----|----------|-------|----------|--------|------|
| T1-REVIEW-01 | review_knowledge | review existing lesson | last_reviewed refreshed and access_count=2 | last_reviewed_changed=True, access_count=2 | PASS |
| T1-REVIEW-02 | review_knowledge | review nonexistent_id | error | {'error': 'Item not found: nonexistent_id'} | PASS |
| T1-REVIEW-03 | review_knowledge | review same lesson again | 3 | 3 | PASS |
| T1-STALE-01 | get_stale_knowledge | days=30 | ['c037954e2afe'] | ['c037954e2afe'] | PASS |
| T1-STALE-02 | get_stale_knowledge | days=90 | [] | [] | PASS |
| T1-STALE-03 | get_stale_knowledge | days=30, limit=0 | [] | [] | PASS |
| T1-HEALTH-01 | health_report | access_count=5, last_reviewed=60d | 82f39616a1c5 | ['82f39616a1c5'] | PASS |
| T1-HEALTH-02 | health_report | access_count=0, last_reviewed=90d | 12fa7fd69969 | ['12fa7fd69969'] | PASS |
| T1-CONTEXT-01 | user_context_warning | 6 stale lessons | warning present | warning_present=True; stale_before=6; stale_after=6; context= ## 经验领域 - lifecycle: 6 个项目经验  ## 相关经验教训（请在开发中主动避免） - round9 kilo stale context sixth uniqr95 - round9 juliet stale context fifth uniqr94  ## stale_knowledge_w | PASS |
| T1-CONTEXT-02 | user_context_warning | 3 stale lessons | warning absent | warning_present=False; stale_before=3; stale_after=3; context= ## 经验领域 - lifecycle: 3 个项目经验  ## 相关经验教训（请在开发中主动避免） - round9 november stale context three uniqr92 - round9 mike stale context two uniqr91 | PASS |

## 3. T2 回归结果

- Round 9：20/20（100.0%）
- 回归数：0
- 判定：pass

| 来源组 | Round 9 | Round 6 同场景 |
|--------|---------|----------------|
| G1 | 5/5 | 5/5 |
| G2 | 5/5 | 4/5 |
| G3 | 5/5 | 5/5 |
| G4 | 5/5 | 5/5 |

| id | expected | actual | Round 6 correct | 回归 | votes | reasoning |
|----|----------|--------|-----------------|------|-------|-----------|
| R9-G1-USER-CONTEXT-01 | get_user_context | get_user_context | True | PASS | ['get_user_context', 'get_user_context', 'get_user_context'] | 用户明确要求加载完整个性化上下文，包括身份、工作方式、偏好、质量标准和关键决策，get_user_context 是提供完整冷启动上下文的工具，最适合此需求。 |
| R9-G1-IDENTITY-CARD-01 | get_identity_card | get_identity_card | True | PASS | ['get_identity_card', 'get_identity_card', 'get_identity_card'] | 用户要求导出一份可携带的 Markdown 身份卡，可以分享给其他 AI 工具，get_identity_card 正好提供此功能。 |
| R9-G1-PROJECT-CONTEXT-01 | get_project_context | get_project_context | True | PASS | ['get_project_context', 'get_project_context', 'get_project_context'] | 用户明确要求读取指定项目文件夹的历史上下文和项目快照，get_project_context 正是用于此目的的工具，参数 project_folder 已提供。 |
| R9-G1-SAVE-SNAPSHOT-01 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，提供了项目文件夹路径 E:/Personal Intelligence Identity Asset/engram，但缺少 data_json 参数（需要包含入口文件、测试命令和已知问题等信息）。 |
| R9-G1-DOMAINS-01 | get_domains | get_domains | True | PASS | ['get_domains', 'get_domains', 'get_domains'] | 用户想查看自己的技术领域经验图谱，了解在哪些 domain 上积累最多，get_domains 正是获取用户技术领域经验图谱的工具。 |
| R9-G2-ADD-LESSON-01 | add_lesson | add_lesson | True | PASS | ['add_lesson', 'add_lesson', 'add_lesson'] | 用户明确要求记录一条经验教训，提供了摘要内容，但缺少domain、source_tool等可选参数，不过这些不是必需的，可以直接调用。 |
| R9-G2-ADD-DECISION-01 | add_decision | add_decision | True | PASS | ['add_decision', 'add_decision', 'add_decision'] | 用户明确说出一条关键决策（Engram v3.7.0 保留39个原子工具，不上线五工具coordinator），适合用add_decision记录。缺少source_tool和domain参数，但question和choice已明确。 |
| R9-G2-SEARCH-01 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户明确给出了搜索关键词 'DeepSeek 工具选择 稳定性'，希望找到相关的经验和决策。search_knowledge 工具最适合按关键词搜索经验教训和决策。参数 query 需要用户提供，但用户已给出搜索词，可以直接使用。 |
| R9-G2-RELEVANT-01 | get_relevant_knowledge | get_relevant_knowledge | True | PASS | ['get_relevant_knowledge', 'get_relevant_knowledge', 'get_relevant_knowledge'] | 用户提供了项目路径，要求自动推荐最相关的历史经验，且没有提供搜索词，因此最适合调用 get_relevant_knowledge，参数 project_folder 已明确给出。 |
| R9-G2-GET-DECISIONS-01 | get_decisions | get_decisions | False | PASS | ['get_decisions', 'get_decisions', 'get_decisions'] | 用户想列出 architecture 领域的关键决策和推理，get_decisions 支持按 domain 过滤，且返回的决策包含 reasoning 字段，最适合此需求。 |
| R9-G3-ARCHIVE-01 | archive_knowledge | archive_knowledge | True | PASS | ['archive_knowledge', 'archive_knowledge', 'archive_knowledge'] | 用户要求归档一条过时的经验（lesson-2026-old-duplicate），archive_knowledge 可以按 ID 归档知识条目，且不会删除数据。 |
| R9-G3-MERGE-01 | merge_knowledge | merge_knowledge | True | PASS | ['merge_knowledge', 'merge_knowledge', 'merge_knowledge'] | 用户要求将 lesson-duplicate-2 合并进 lesson-duplicate-1，并保留主条目 ID，这正好对应 merge_knowledge 工具的功能：将 secondary 合并到 primary 并归档 secondary。 |
| R9-G3-AUDIT-01 | get_audit_log | get_audit_log | True | PASS | ['get_audit_log', 'get_audit_log', 'get_audit_log'] | 用户请求查看最近20条Engram审计日志，get_audit_log工具可以返回最近的审计日志条目，limit参数默认为50，用户要求20条，但工具会返回最近的条目，用户可以从结果中取前20条。 |
| R9-G3-WRAP-SESSION-01 | wrap_up_session | wrap_up_session | True | PASS | ['wrap_up_session', 'wrap_up_session', 'wrap_up_session'] | 用户明确要求会话结束收尾，自动提取经验和决策并保存项目快照，wrap_up_session 正是为此设计的工具。但缺少必填参数 summary 和 source_tool，以及可选的 project_folder。请提供会话摘要和来源工具名称。 |
| R9-G3-START-PROJECT-01 | start_project | start_project | True | PASS | ['start_project', 'start_project', 'start_project'] | 用户明确要求启动新项目，需要继承历史经验并创建项目档案，start_project 正是为此设计的工具。但缺少必填参数 description 和 project_folder，需要用户提供项目描述和文件夹路径。 |
| R9-G4-NONE-01 | none | none | True | PASS | ['none', 'none', 'none'] | 用户只是问候并询问天气，与 Engram 的知识管理功能无关，不需要调用任何工具。 |
| R9-G4-NONE-02 | none | none | True | PASS | ['none', 'none', 'none'] | 用户只是询问 Engram 是什么，要求解释，没有要求读取、写入或维护知识库，属于一般性聊天/解释请求，不需要调用任何 Engram MCP 工具。 |
| R9-G4-MISSING-01 | add_lesson | add_lesson | True | PASS | ['add_lesson', 'add_lesson', 'add_lesson'] | 用户明确要求记录一条教训，但未提供具体内容（summary等参数）。需要用户补充教训摘要等信息才能完成记录。 |
| R9-G4-MISSING-02 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户明确要求搜索相关经验，search_knowledge 工具最适合按关键词搜索经验教训和决策。但用户未提供搜索关键词（query 参数），需要用户补充搜索内容。 |
| R9-G4-MISSING-05 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，但缺少必填参数 project_folder 和 data_json。需要用户提供项目文件夹路径和要保存的数据。 |

## 4. 综合判定

**通过**：T1 全部通过；T2 达到门槛。
