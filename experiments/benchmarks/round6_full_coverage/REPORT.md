# Engram Round 6：39 工具全量覆盖自测报告

## 1. 运行说明

- LLM：DeepSeek `deepseek-chat`
- 温度：0.0
- 每个场景：3 次调用取多数
- 当前工具数：39（从 `src/engram_core/mcp_server.py` 实时抽取）
- 总场景数：88
- 总调用次数：264
- 失败调用：0
- Usage：prompt=733260，completion=15343，total=748603 tokens
- 边界：本轮只做验证；未修改 `src/engram_core/mcp_server.py` 主代码。
- 原始响应：`results_raw.jsonl` 合并保存全部 request/response/raw_content/parsed/error。

## 2. G1 Identity & Context 结果

- 总体：24/24（100.0%，pass）；0/2 工具数：0

| 工具 | 正确 | 准确率 | 判定 |
|------|------|--------|------|
| `get_domains` | 2/2 | 100.0% | pass |
| `get_identity_card` | 2/2 | 100.0% | pass |
| `get_preferences` | 2/2 | 100.0% | pass |
| `get_profile` | 2/2 | 100.0% | pass |
| `get_project_context` | 2/2 | 100.0% | pass |
| `get_quality_standards` | 2/2 | 100.0% | pass |
| `get_trust_boundaries` | 2/2 | 100.0% | pass |
| `get_user_context` | 2/2 | 100.0% | pass |
| `get_work_style` | 2/2 | 100.0% | pass |
| `list_projects` | 2/2 | 100.0% | pass |
| `save_project_snapshot` | 2/2 | 100.0% | pass |
| `update_identity` | 2/2 | 100.0% | pass |

## 3. G2 Knowledge Recording & Retrieval 结果

- 总体：29/30（96.7%，pass）；0/2 工具数：0

| 工具 | 正确 | 准确率 | 判定 |
|------|------|--------|------|
| `add_decision` | 2/2 | 100.0% | pass |
| `add_lesson` | 2/2 | 100.0% | pass |
| `bulk_add_knowledge` | 2/2 | 100.0% | pass |
| `export_knowledge_report` | 2/2 | 100.0% | pass |
| `extract_session_insights` | 2/2 | 100.0% | pass |
| `find_similar_knowledge` | 2/2 | 100.0% | pass |
| `get_decisions` | 1/2 | 50.0% | pass |
| `get_knowledge_inheritance` | 2/2 | 100.0% | pass |
| `get_knowledge_overview` | 2/2 | 100.0% | pass |
| `get_lessons` | 2/2 | 100.0% | pass |
| `get_related_knowledge` | 2/2 | 100.0% | pass |
| `get_relevant_knowledge` | 2/2 | 100.0% | pass |
| `ingest_notes` | 2/2 | 100.0% | pass |
| `search_knowledge` | 2/2 | 100.0% | pass |
| `update_knowledge` | 2/2 | 100.0% | pass |

与 Round 5 近义组对比：

| Round 5 近义组 | Round 5 | Round 6 覆盖工具 | Round 6 | 差异 |
|----------------|---------|------------------|---------|------|
| lesson_decision_extract | 100.0% | `add_lesson`, `add_decision`, `extract_session_insights` | 100.0% | +0.0pp |
| search_relevant_similar | 100.0% | `search_knowledge`, `get_relevant_knowledge`, `find_similar_knowledge` | 100.0% | +0.0pp |
| snapshot_context_user | 100.0% | `save_project_snapshot`, `get_project_context`, `get_user_context` | 100.0% | +0.0pp |

## 4. G3 Maintenance, Session & Workflow 结果

- 总体：24/24（100.0%，pass）；0/2 工具数：0
- 快捷工具误抢原子工具：0
- 快捷工具场景误选原子工具：0

| 工具 | 正确 | 准确率 | 判定 |
|------|------|--------|------|
| `archive_knowledge` | 2/2 | 100.0% | pass |
| `export_engram` | 2/2 | 100.0% | pass |
| `export_engram_to_openclaw` | 2/2 | 100.0% | pass |
| `get_audit_log` | 2/2 | 100.0% | pass |
| `import_engram` | 2/2 | 100.0% | pass |
| `import_engram_from_openclaw` | 2/2 | 100.0% | pass |
| `link_knowledge` | 2/2 | 100.0% | pass |
| `merge_knowledge` | 2/2 | 100.0% | pass |
| `read_web_content` | 2/2 | 100.0% | pass |
| `start_project` | 2/2 | 100.0% | pass |
| `unlink_knowledge` | 2/2 | 100.0% | pass |
| `wrap_up_session` | 2/2 | 100.0% | pass |

快捷工具 vs 原子工具混淆：

无快捷工具与原子工具混淆。

## 5. G4 边界场景结果

- no-tool：5/5（100.0%，pass）
- 缺参数：5/5（100.0%，pass）

| id | 类型 | expected | actual | 正确 | reasoning |
|----|------|----------|--------|------|-----------|
| G4-NONE-01 | no_tool | none | none | PASS | 用户询问天气，这是一个普通的聊天问题，与 Engram 的知识管理功能无关，不需要调用任何工具。 |
| G4-NONE-02 | no_tool | none | none | PASS | 用户只是询问 Engram 是什么，要求一般性解释，没有要求读取、写入或维护知识库中的任何内容，因此不需要调用任何 Engram MCP 工具。 |
| G4-NONE-03 | no_tool | none | none | PASS | 用户是在询问对之前提到的方案的评价意见，属于一般性对话交流，没有明确要求读取或写入Engram知识库中的任何内容。 |
| G4-NONE-04 | no_tool | none | none | PASS | 用户表示还在思考，没有提出任何需要调用 Engram 工具的具体请求。 |
| G4-NONE-05 | no_tool | none | none | PASS | 用户只是表示感谢，没有要求读取、写入或维护任何知识，不需要调用任何Engram工具。 |
| G4-MISSING-01 | missing_params | add_lesson | add_lesson | PASS | 用户明确要求记录一条教训，但未提供具体内容（summary等必要参数），因此选择add_lesson并指出缺少参数。 |
| G4-MISSING-02 | missing_params | search_knowledge | search_knowledge | PASS | 用户明确要求搜索相关经验，search_knowledge 最适合按关键词搜索经验教训和决策，但缺少必需的 query 参数。 |
| G4-MISSING-03 | missing_params | merge_knowledge | merge_knowledge | PASS | 用户要求合并两条知识，merge_knowledge 工具正是用于合并两条知识条目（将次要条目的关联转移到主要条目后归档次要条目）。但缺少 primary_id 和 secondary_id 参数，需要用户提供要合并的两条知识的 ID。 |
| G4-MISSING-04 | missing_params | export_engram_to_openclaw | export_engram_to_openclaw | PASS | 用户明确要求导出到OpenClaw，对应工具export_engram_to_openclaw，参数output_dir可选，不提供则使用默认路径。 |
| G4-MISSING-05 | missing_params | save_project_snapshot | save_project_snapshot | PASS | 用户明确要求保存项目快照，但缺少必填参数 project_folder 和 data_json。需要用户提供项目文件夹路径和要保存的数据。 |

## 6. 综合判定

**通过**：G1/G2/G3 均达到全量工具选择门槛，G4 no-tool 与缺参数场景也达到通过线。

## 7. 全量覆盖热力图

| 工具 | 结果 | 准确率 | 状态 |
|------|------|--------|------|
| `get_user_context` | 2/2 | 100.0% | OK |
| `get_identity_card` | 2/2 | 100.0% | OK |
| `get_profile` | 2/2 | 100.0% | OK |
| `get_work_style` | 2/2 | 100.0% | OK |
| `get_preferences` | 2/2 | 100.0% | OK |
| `get_trust_boundaries` | 2/2 | 100.0% | OK |
| `get_quality_standards` | 2/2 | 100.0% | OK |
| `update_identity` | 2/2 | 100.0% | OK |
| `get_project_context` | 2/2 | 100.0% | OK |
| `list_projects` | 2/2 | 100.0% | OK |
| `save_project_snapshot` | 2/2 | 100.0% | OK |
| `get_domains` | 2/2 | 100.0% | OK |
| `add_lesson` | 2/2 | 100.0% | OK |
| `add_decision` | 2/2 | 100.0% | OK |
| `extract_session_insights` | 2/2 | 100.0% | OK |
| `ingest_notes` | 2/2 | 100.0% | OK |
| `bulk_add_knowledge` | 2/2 | 100.0% | OK |
| `update_knowledge` | 2/2 | 100.0% | OK |
| `search_knowledge` | 2/2 | 100.0% | OK |
| `get_relevant_knowledge` | 2/2 | 100.0% | OK |
| `find_similar_knowledge` | 2/2 | 100.0% | OK |
| `get_knowledge_inheritance` | 2/2 | 100.0% | OK |
| `get_lessons` | 2/2 | 100.0% | OK |
| `get_decisions` | 1/2 | 50.0% | WARN |
| `get_related_knowledge` | 2/2 | 100.0% | OK |
| `get_knowledge_overview` | 2/2 | 100.0% | OK |
| `export_knowledge_report` | 2/2 | 100.0% | OK |
| `archive_knowledge` | 2/2 | 100.0% | OK |
| `merge_knowledge` | 2/2 | 100.0% | OK |
| `link_knowledge` | 2/2 | 100.0% | OK |
| `unlink_knowledge` | 2/2 | 100.0% | OK |
| `export_engram` | 2/2 | 100.0% | OK |
| `import_engram` | 2/2 | 100.0% | OK |
| `export_engram_to_openclaw` | 2/2 | 100.0% | OK |
| `import_engram_from_openclaw` | 2/2 | 100.0% | OK |
| `get_audit_log` | 2/2 | 100.0% | OK |
| `read_web_content` | 2/2 | 100.0% | OK |
| `wrap_up_session` | 2/2 | 100.0% | OK |
| `start_project` | 2/2 | 100.0% | OK |

需要进一步优化 docstring 的工具：

- `get_decisions`：1/2，建议补强与相邻工具的边界描述。

## 8. 与 Round 3/5 对比

- Round 3 Test E：37 工具近义场景总体 90.0%。
- Round 5 T1：近义场景总体 100.0%；T2：`wrap_up_session` 与 `start_project` 均 100.0%；T3：14/15，发现 1 个快捷工具抢占回归。
- Round 6：覆盖 39/39 工具，每个工具 2 个场景；G1=100.0%，G2=96.7%，G3=100.0%，G4=100.0%。
- Round 6 新增了 `none` 选项以测试闲聊/澄清/感谢类 no-tool 场景，并加入缺参数场景以观察模型是否会乱猜参数。

## 附：失败场景明细

| id | expected | actual | votes | reasoning |
|----|----------|--------|-------|-----------|
| G2-GET-DECISIONS-01 | get_decisions | search_knowledge | ['search_knowledge', 'search_knowledge', 'get_decisions'] | 用户想列出 architecture 领域的关键决策和推理，search_knowledge 可以通过关键词搜索经验教训和决策，适合此需求。但缺少 query 参数，用户未提供具体搜索词，不过可以推断 query 为 'architecture'。scope 和 limit 可选，默认即可。 |
