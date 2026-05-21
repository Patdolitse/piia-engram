# Engram Round 7：Domain 软化验证报告

## 1. 运行说明

- 范围：验证 domain 多标签软化；未修改主代码。
- T1：Python 直接测试 `Engram(root=tempdir)`，不用 LLM，不污染用户数据。
- T2：DeepSeek `deepseek-chat`，从 Round 6 抽取 20 个核心场景，每场景 3 次取多数。
- T2 工具数：39（从 `src/engram_core/mcp_server.py` 实时抽取）。
- T2 调用次数：60，失败调用：0。
- T2 usage：prompt=169896，completion=3203，total=173099 tokens。

## 2. T1 单元测试结果

- 总体：15/15，pass

| 分组 | 结果 | 判定 |
|------|------|------|
| infer | 6/6 | pass |
| contains_filter | 4/4 | pass |
| compatibility | 3/3 | pass |
| domain_counts | 2/2 | pass |

| id | category | input | expected | actual | 正确 |
|----|----------|-------|----------|--------|------|
| T1-INFER-01 | infer | pytest configuration for Python project | python | python | PASS |
| T1-INFER-02 | infer | docker compose for node.js app | docker,javascript | javascript,docker | PASS |
| T1-INFER-03 | infer | git rebase strategy for architecture refactor | git,architecture | git,architecture | PASS |
| T1-INFER-04 | infer | random unrelated text | fallback | fallback | PASS |
| T1-INFER-05 | infer | MCP tool server design pattern | mcp,architecture | mcp,architecture | PASS |
| T1-INFER-06 | infer | SQL migration for Django | database,python | python,database | PASS |
| T1-FILTER-01 | contains_filter | domain=python | True | True | PASS |
| T1-FILTER-02 | contains_filter | domain=testing | True | True | PASS |
| T1-FILTER-03 | contains_filter | domain=javascript | False | False | PASS |
| T1-FILTER-04 | contains_filter | domain=None | True | True | PASS |
| T1-COMPAT-01 | compatibility | single domain lesson with domain=python | True | True | PASS |
| T1-COMPAT-02 | compatibility | empty domain lesson with domain=python filter | False | False | PASS |
| T1-COMPAT-03 | compatibility | unfiltered lessons include empty-domain lesson | True | True | PASS |
| T1-COUNT-01 | domain_counts | python count | 2 | 2 | PASS |
| T1-COUNT-02 | domain_counts | testing count | 1 | 1 | PASS |

## 3. T2 回归结果

- Round 7：19/20（95.0%）
- Round 6 同场景基线：19/20（95.0%）
- 回归数：0
- 判定：pass

| 来源组 | Round 7 | Round 6 同场景 |
|--------|---------|----------------|
| G1 | 5/5 | 5/5 |
| G2 | 4/5 | 4/5 |
| G3 | 5/5 | 5/5 |
| G4 | 5/5 | 5/5 |

| id | expected | actual | Round 6 correct | 回归 | votes | reasoning |
|----|----------|--------|-----------------|------|-------|-----------|
| R7-G1-USER-CONTEXT-01 | get_user_context | get_user_context | True | PASS | ['get_user_context', 'get_user_context', 'get_user_context'] | 用户明确要求加载完整个性化上下文，包括身份、工作方式、偏好、质量标准和关键决策，get_user_context 是提供完整冷启动上下文的工具，最适合此需求。 |
| R7-G1-IDENTITY-CARD-01 | get_identity_card | get_identity_card | True | PASS | ['get_identity_card', 'get_identity_card', 'get_identity_card'] | 用户要求导出一份可携带的 Markdown 身份卡，get_identity_card 正是为此设计的工具，无需额外参数。 |
| R7-G1-PROJECT-CONTEXT-01 | get_project_context | get_project_context | True | PASS | ['get_project_context', 'get_project_context', 'get_project_context'] | 用户明确要求读取指定项目文件夹的历史上下文和项目快照，get_project_context 正是用于读取项目级知识快照的工具，参数 project_folder 已提供为 'E:/Personal Intelligence Identity Asset/engram'。 |
| R7-G1-SAVE-SNAPSHOT-01 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，提供了项目文件夹路径，但缺少 data_json 参数（需要包含入口文件、测试命令和已知问题等信息）。 |
| R7-G1-DOMAINS-01 | get_domains | get_domains | True | PASS | ['get_domains', 'get_domains', 'get_domains'] | 用户明确要求查看技术领域经验图谱，get_domains 工具正好返回用户涉及的所有技术/领域及其积累情况，完全匹配需求。 |
| R7-G2-ADD-LESSON-01 | add_lesson | add_lesson | True | PASS | ['add_lesson', 'add_lesson', 'add_lesson'] | 用户明确要求记录一条经验教训，提供了摘要和领域信息，符合 add_lesson 的使用场景。参数 summary 和 domain 已提供，detail 和 source_tool 可选但未提供，不强制。 |
| R7-G2-ADD-DECISION-01 | add_decision | add_decision | True | PASS | ['add_decision', 'add_decision', 'add_decision'] | 用户明确说出一条关键决策（Engram v3.7.0 保留39个原子工具，不上线五工具coordinator），应使用add_decision记录。缺少source_tool和project参数，但用户未提供，不虚构。 |
| R7-G2-SEARCH-01 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户明确给出了搜索关键词 'DeepSeek 工具选择 稳定性'，希望找到相关的经验和决策。search_knowledge 支持按关键词搜索经验教训和决策，最适合此需求。 |
| R7-G2-RELEVANT-01 | get_relevant_knowledge | get_relevant_knowledge | True | PASS | ['get_relevant_knowledge', 'get_relevant_knowledge', 'get_relevant_knowledge'] | 用户明确要求根据当前项目路径自动推荐最相关的历史经验，且不提供搜索词，这正是 get_relevant_knowledge 的用途。项目路径已提供：E:/Personal Intelligence Identity Asset/engram。 |
| R7-G2-GET-DECISIONS-01 | get_decisions | search_knowledge | False | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户想列出 architecture 领域的关键决策和推理，search_knowledge 支持按关键词搜索决策内容，scope 可设为 'decisions' 来精准匹配，query 参数为 'architecture'。 |
| R7-G3-EXPORT-ENGRAM-01 | export_engram | export_engram | True | PASS | ['export_engram', 'export_engram', 'export_engram'] | 用户要求将整个 Engram 导出为备份文件，export_engram 工具正是为此设计的，且不需要额外参数（output_path 可选）。 |
| R7-G3-EXPORT-OPENCLAW-01 | export_engram_to_openclaw | export_engram_to_openclaw | True | PASS | ['export_engram_to_openclaw', 'export_engram_to_openclaw', 'export_engram_to_openclaw'] | 用户明确要求导出 Engram 为 OpenClaw 兼容格式，生成 SOUL.md、MEMORY.md 和 USER.md 文件，对应工具 export_engram_to_openclaw 的用途完全匹配。 |
| R7-G3-AUDIT-01 | get_audit_log | get_audit_log | True | PASS | ['get_audit_log', 'get_audit_log', 'get_audit_log'] | 用户明确要求查看最近20条Engram审计日志，get_audit_log工具支持limit参数，默认返回50条，用户要求20条，因此调用时传入limit=20。 |
| R7-G3-WRAP-SESSION-01 | wrap_up_session | wrap_up_session | True | PASS | ['wrap_up_session', 'wrap_up_session', 'wrap_up_session'] | 用户明确要求会话结束收尾，自动提取经验和决策并保存项目快照，wrap_up_session 正是为此设计的工具。但缺少必填参数 summary 和 source_tool，以及可选的 project_folder。请用户提供会话摘要和来源工具名称。 |
| R7-G3-START-PROJECT-01 | start_project | start_project | True | PASS | ['start_project', 'start_project', 'start_project'] | 用户明确要求启动新项目并继承历史经验、创建项目档案，start_project 正是为此设计的工具。但缺少必填参数 description 和 project_folder，需要用户补充项目描述和文件夹路径。 |
| R7-G4-NONE-01 | none | none | True | PASS | ['none', 'none', 'none'] | 用户询问天气，这是一个普通的聊天问题，与 Engram 的知识管理功能无关，不需要调用任何工具。 |
| R7-G4-NONE-02 | none | none | True | PASS | ['none', 'none', 'none'] | 用户询问 Engram 是什么，这是一个一般性的解释性问题，不涉及读取、写入或维护 Engram 知识库的具体操作，因此不需要调用任何 Engram MCP 工具。 |
| R7-G4-MISSING-01 | add_lesson | add_lesson | True | PASS | ['add_lesson', 'add_lesson', 'add_lesson'] | 用户明确要求记录一条教训，应使用 add_lesson 工具。但缺少必要参数 summary（教训摘要），需要用户提供具体内容。 |
| R7-G4-MISSING-02 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户明确要求搜索相关经验，search_knowledge 最适合按关键词搜索经验教训和决策，但缺少必填参数 query，需要用户提供搜索词。 |
| R7-G4-MISSING-05 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，但缺少必填参数 project_folder 和 data_json。需要用户提供项目文件夹路径和要保存的数据。 |

## 4. 综合判定

**通过**：T1 全部通过；T2 无准确率回退且达到门槛。
