# Engram Round 8：get_decisions domain 参数验证报告

## 1. 运行说明

- 范围：验证 `add_decision` 写入 domain 与 `get_decisions(domain=...)` 包含匹配过滤；未修改主代码。
- T1：Python 直接测试 `Engram(root=tempdir)`，不用 LLM，不污染用户数据。
- T2：DeepSeek `deepseek-chat`，从 Round 6 抽取 20 个核心场景，每场景 3 次取多数。
- T2 抽样强制包含 `G2-GET-DECISIONS-01` 和 `G2-GET-DECISIONS-02`。
- T2 工具数：39（从 `src/piia_engram/mcp_server.py` 实时抽取）。
- T2 调用次数：60，失败调用：0。
- T2 usage：prompt=172911，completion=3299，total=176210 tokens。

## 2. T1 单元测试结果

- 总体：8/8，pass

| 分组 | 结果 | 判定 |
|------|------|------|
| write | 2/2 | pass |
| filter | 4/4 | pass |
| compatibility | 2/2 | pass |

| id | category | input | expected | actual | 正确 |
|----|----------|-------|----------|--------|------|
| T1-WRITE-01 | write | add_decision(domain='architecture') | architecture | architecture | PASS |
| T1-WRITE-02 | write | add_decision(domain='architecture,database') | architecture,database | architecture,database | PASS |
| T1-FILTER-01 | filter | domain=architecture | ['c6fd50049642', '7d6ef08cf68b'] | ['c6fd50049642', '7d6ef08cf68b'] | PASS |
| T1-FILTER-02 | filter | domain=database | ['7d6ef08cf68b'] | ['7d6ef08cf68b'] | PASS |
| T1-FILTER-03 | filter | domain=python | [] | [] | PASS |
| T1-FILTER-04 | filter | domain=None | ['c6fd50049642', '7d6ef08cf68b', '12f96d560dc1'] | ['c6fd50049642', '7d6ef08cf68b', '12f96d560dc1'] | PASS |
| T1-COMPAT-01 | compatibility | unfiltered get_decisions includes no-domain decision | True | True | PASS |
| T1-COMPAT-02 | compatibility | domain=architecture excludes no-domain decision | False | False | PASS |

## 3. T2 回归结果

- Round 8：20/20（100.0%）
- Round 6 同场景基线：19/20（95.0%）
- 回归数：0
- 判定：pass

| 来源组 | Round 8 | Round 6 同场景 |
|--------|---------|----------------|
| G1 | 5/5 | 5/5 |
| G2 | 5/5 | 4/5 |
| G3 | 5/5 | 5/5 |
| G4 | 5/5 | 5/5 |

| id | expected | actual | Round 6 correct | 回归 | votes | reasoning |
|----|----------|--------|-----------------|------|-------|-----------|
| R8-G1-USER-CONTEXT-01 | get_user_context | get_user_context | True | PASS | ['get_user_context', 'get_user_context', 'get_user_context'] | 用户明确要求加载完整个性化上下文，包括身份、工作方式、偏好、质量标准和关键决策。get_user_context 是提供完整冷启动上下文的工具，最适合此需求。用户未指定项目文件夹，因此不传 project_folder 参数。 |
| R8-G1-IDENTITY-CARD-01 | get_identity_card | get_identity_card | True | PASS | ['get_identity_card', 'get_identity_card', 'get_identity_card'] | 用户要求导出一份可以发给另一个 AI 的 Markdown 身份卡，内容要精简可携带，get_identity_card 正好提供自包含的 AI 身份卡（Markdown 格式），可分享给任何 AI 工具。 |
| R8-G1-PROJECT-CONTEXT-01 | get_project_context | get_project_context | True | PASS | ['get_project_context', 'get_project_context', 'get_project_context'] | 用户明确要求读取指定项目的历史上下文和项目快照，get_project_context 正是用于读取项目级知识快照的工具，参数 project_folder 已提供为 'E:/Personal Intelligence Identity Asset/engram'。 |
| R8-G1-SAVE-SNAPSHOT-01 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，提供了项目文件夹路径，但缺少 data_json 参数（需要包含入口文件、测试命令和已知问题等信息）。 |
| R8-G1-DOMAINS-01 | get_domains | get_domains | True | PASS | ['get_domains', 'get_domains', 'get_domains'] | 用户明确要求查看技术领域经验图谱，get_domains 工具正好返回用户涉及的所有技术/领域及其积累情况，完全匹配需求。 |
| R8-G2-ADD-DECISION-01 | add_decision | add_decision | True | PASS | ['add_decision', 'add_decision', 'add_decision'] | 用户明确要求记录一条关键决策，内容是关于Engram v3.7.0保留39个原子工具、不上线五工具coordinator。应使用add_decision工具，但缺少source_tool参数（建议填写），domain可推断为'architecture,design'。 |
| R8-G2-SEARCH-01 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户明确要求按关键词搜索经验教训和决策，search_knowledge 最适合此场景。参数 query 应为 'DeepSeek 工具选择 稳定性'，scope 和 limit 可选。用户未提供 scope 和 limit，但意图明确，因此选择该工具并指出缺少可选参数。 |
| R8-G2-RELEVANT-01 | get_relevant_knowledge | get_relevant_knowledge | True | PASS | ['get_relevant_knowledge', 'get_relevant_knowledge', 'get_relevant_knowledge'] | 用户明确要求根据当前项目路径自动推荐最相关的历史经验，且不提供搜索词，这正是 get_relevant_knowledge 工具的用途。用户提供了项目路径 E:/Personal Intelligence Identity Asset/engram，参数完整。 |
| R8-G2-GET-DECISIONS-01 | get_decisions | get_decisions | False | PASS | ['get_decisions', 'get_decisions', 'get_decisions'] | 用户想列出 architecture 领域的关键决策和推理，get_decisions 支持按 domain 过滤，且返回的决策包含 reasoning 字段，最适合此需求。 |
| R8-G2-GET-DECISIONS-02 | get_decisions | get_decisions | True | PASS | ['get_decisions', 'get_decisions', 'get_decisions'] | 用户想回顾最近的决策记录，明确要求只看 decisions 不要 lessons，get_decisions 工具正好按时间列出关键决策，无需搜索词，符合需求。 |
| R8-G3-EXPORT-ENGRAM-01 | export_engram | export_engram | True | PASS | ['export_engram', 'export_engram', 'export_engram'] | 用户要求将整个 Engram 导出为内部备份文件，export_engram 工具正是用于此目的，且无需额外参数（output_path 可选）。 |
| R8-G3-EXPORT-OPENCLAW-01 | export_engram_to_openclaw | export_engram_to_openclaw | True | PASS | ['export_engram_to_openclaw', 'export_engram_to_openclaw', 'export_engram_to_openclaw'] | 用户明确要求导出 Engram 数据为 OpenClaw 兼容格式（SOUL.md、MEMORY.md、USER.md），对应工具 export_engram_to_openclaw 完全匹配此需求。该工具无必需参数，可直接调用。 |
| R8-G3-AUDIT-01 | get_audit_log | get_audit_log | True | PASS | ['get_audit_log', 'get_audit_log', 'get_audit_log'] | 用户请求查看最近20条Engram审计日志，get_audit_log工具正好提供此功能，且limit参数可设为20。 |
| R8-G3-WRAP-SESSION-01 | wrap_up_session | wrap_up_session | True | PASS | ['wrap_up_session', 'wrap_up_session', 'wrap_up_session'] | 用户明确要求会话结束收尾，需要自动提取经验和决策并保存项目快照，wrap_up_session 正好同时完成这两件事。但缺少必填参数 summary 和 source_tool，以及可选的 project_folder。 |
| R8-G3-START-PROJECT-01 | start_project | start_project | True | PASS | ['start_project', 'start_project', 'start_project'] | 用户明确要求启动新项目并继承历史经验、创建项目档案，start_project 正好提供这两项功能。但缺少必填参数 description 和 project_folder，需要用户补充项目描述和文件夹路径。 |
| R8-G4-NONE-01 | none | none | True | PASS | ['none', 'none', 'none'] | 用户询问天气，这是一个普通的聊天问题，与Engram的知识管理功能无关，不需要调用任何工具。 |
| R8-G4-NONE-02 | none | none | True | PASS | ['none', 'none', 'none'] | 用户询问 Engram 是什么，这是一个一般性的解释性问题，不涉及读取或写入知识库的操作，因此不需要调用任何 Engram MCP 工具。 |
| R8-G4-MISSING-01 | add_lesson | add_lesson | True | PASS | ['add_lesson', 'add_lesson', 'add_lesson'] | 用户明确要求记录一条教训，但未提供具体内容（summary等参数），需要用户补充教训内容。 |
| R8-G4-MISSING-02 | search_knowledge | search_knowledge | True | PASS | ['search_knowledge', 'search_knowledge', 'search_knowledge'] | 用户说'搜索一下相关经验'，明确表示想搜索经验教训，但没有提供搜索词（query参数缺失）。search_knowledge最适合按关键词搜索经验教训和决策，但缺少必填参数query。 |
| R8-G4-MISSING-05 | save_project_snapshot | save_project_snapshot | True | PASS | ['save_project_snapshot', 'save_project_snapshot', 'save_project_snapshot'] | 用户明确要求保存项目快照，但缺少必填参数 project_folder 和 data_json。需要用户提供项目文件夹路径和要保存的数据。 |

## 4. 综合判定

**通过**：T1 全部通过；T2 无准确率回退且达到门槛。
