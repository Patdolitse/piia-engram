# Engram 第四轮回归基准报告

## 1. 运行说明

- LLM：DeepSeek（`deepseek-chat`）
- 温度：0.0
- 每个评估场景：3 次取多数或中位数
- 总调用次数：225
- 失败/UNKNOWN 调用：0
- Usage：prompt=129615，completion=17417，total=147032 tokens
- 成本：记录 API usage，不按实时价格换算；实际费用以 DeepSeek 控制台为准。
- R1 场景数：5；R2 调用数：10；R2 相似度对数：45；R3 场景数：15
- 边界：本轮只新增 `experiments/benchmarks/round4_regression/`，未修改主代码或前三轮 benchmark。

## 2. R1 onboarding 数据完整性

| 用户类型 | role | tech stack | lesson search | language | 说明 |
|---|---:|---:|---:|---:|---|
| R1-ZH-BACKEND | PASS | PASS | PASS | PASS | The cold-start context accurately reflects the user's role as '后端开发者', tech stack details (Python, FastAPI, pytest, PostgreSQL), and language (Chinese). The seeded lesson about running pytest before database migrations is present in both th |
| R1-EN-FRONTEND | PASS | PASS | PASS | PASS | The cold-start context correctly preserves the user's role as 'Frontend developer', the tech stack (TypeScript, React, Vite, Playwright), and the language (English). The seeded lesson about running Playwright smoke tests after changing Type |
| R1-ZH-FULLSTACK-MULTI | PASS | PASS | PASS | PASS | The cold-start context accurately reflects the seeded profile's role, tech stack, and language preferences. The search output returns a lesson that matches the second lesson listed in the context, confirming it is both present and searchabl |
| R1-EN-DATA | PASS | PASS | PASS | PASS | The generated cold-start context accurately reflects the seeded role, tech stack, and language. The two seeded lessons are present in the context, and the search output returns one of them with correct semantic content and domain. The langu |
| R1-ZH-CROSS-TOOL | PASS | PASS | PASS | PASS | The cold-start context accurately reflects the user's role as a cross-tool developer, tech stack (Claude Code, Codex, Cursor, MCP, Python), and language (Chinese). The seeded lessons about MCP tool design and cross-tool handoff are both pre |

- role 可读：5/5
- tech stack 可读：5/5
- lesson 可检索：5/5
- 中文组：3/3；英文组：3/3
- R1 判定：PASS

## 3. R2 get_user_context 一致性

- 10 次输出 byte-identical：PASS
- get_user_context/generate_context 调用 LLM：否。当前实现为本地拼装，因此理论上应稳定。
- identity 完整：10/10
- 关键 lesson 提及：10/10
- 关键 decision 提及：10/10
- 关键知识整体提及：10/10
- 最低语义相似度：1.00
- R2 判定：PASS

相似度矩阵：

| call | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 2 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 3 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 4 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 6 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 7 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 8 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 9 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 10 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

## 4. R3 extract_session_insights 质量

- Recall：100.0%
- Precision：83.3%
- 语义准确率：95.0%
- 普通讨论 false positive：2/5
- R3 判定：PASS

| id | 类型 | saved | relevant | false positive | semantic | 说明 |
|---|---|---:|---:|---:|---:|---|
| R3-L01 | lesson | 1 | PASS | PASS | 95.0% | The dialogue describes a recurring issue and a concrete lesson: always verify both the installer and portable zip before publishing a release. The extraction correctly identifies this as a lesson, saves it with a clear summary, and skips th |
| R3-L02 | lesson | 1 | PASS | PASS | 95.0% | The dialogue describes a concrete technical lesson learned from a retrospective: that two metrics (raw_result_count and unique_company_count) must be recorded separately to avoid misleading users about search breadth. This is a durable, act |
| R3-L03 | lesson | 1 | PASS | PASS | 95.0% | The dialogue clearly states a lesson learned about keeping network fakes explicit and labeling them in reports, which is a durable technical rule. The extraction correctly identifies this as a lesson and saves it, while skipping the descrip |
| R3-L04 | lesson | 1 | PASS | PASS | 95.0% | The dialogue clearly states a concrete technical rule: when testing local knowledge bases like Engram, always use a temporary directory instead of the real ~/.engram to avoid polluting user data. This is a durable lesson worth persisting. T |
| R3-L05 | lesson | 2 | PASS | PASS | 95.0% | The dialogue contains two distinct technical rules/lessons learned: (1) browser evidence validity depends on real page interaction, and (2) code-only inspection should be separated from UX pass. Both are durable, non-trivial lessons suitabl |
| R3-D01 | decision | 1 | PASS | PASS | 95.0% | The dialogue contains a concrete decision to drop the coordinator launch path and keep the 37 explicit tools, which is a durable technical decision worth persisting. The extraction correctly identifies it as a saved decision with high seman |
| R3-D02 | decision | 1 | PASS | PASS | 95.0% | The dialogue contains a concrete decision (NO-GO on payment migration pre-drill) with a clear technical condition (until OSS upload/download/decrypt/recovery chain is re-proven). This is a durable decision worth persisting. The extraction c |
| R3-D03 | decision | 1 | PASS | PASS | 95.0% | The dialogue clearly states a concrete technical decision (choosing a local-first project asset graph over a generic vector database) with a rationale (stable file geography and build outputs). This qualifies as a durable memory. The extrac |
| R3-D04 | decision | 1 | PASS | PASS | 95.0% | The dialogue contains a concrete decision about redefining the purpose of the fourth round of benchmark testing, which is a technical rule or decision worth persisting. The extraction correctly identifies it as a saved decision, with high s |
| R3-D05 | decision | 1 | PASS | PASS | 95.0% | The dialogue describes a concrete decision to switch the Android verification target from a mocked API run to a real emulator install, with a clear rationale (version confirmation matters more than a local build log). This qualifies as a du |
| R3-O01 | ordinary | 0 | FAIL | PASS | 100.0% | The dialogue contains only ordinary coordination and casual status updates (checking dashboard, cards loading order, meeting notes location), with no lesson learned, technical rule, or concrete decision. The extraction correctly skipped all |
| R3-O02 | ordinary | 0 | FAIL | PASS | 100.0% | The dialogue explicitly states '这只是一次素材整理，没有形成新的规则或取舍', confirming no durable lesson, technical rule, or concrete decision was made. The extraction correctly skipped both segments, so the output is accurate and not a false positive. |
| R3-O03 | ordinary | 0 | FAIL | PASS | 100.0% | The dialogue contains only a casual status update about an export duration and a plan to rerun later. Neither statement captures a lesson learned, a technical rule, or a concrete decision worth persisting. The extraction correctly skipped b |
| R3-O04 | ordinary | 1 | FAIL | FAIL | 0.0% | The dialogue explicitly states '没有新结论' (no new conclusion) and describes only a routine check of existing documents. The extraction incorrectly treats a conditional plan ('后面如果继续推进，再单独写正式决策记录') as a concrete decision, which is not a durable |
| R3-O05 | ordinary | 1 | FAIL | FAIL | 0.0% | The dialogue contains only a temporary scheduling note ('look at chart labels tomorrow') and a casual status update ('only a visual review note'), with no lesson learned, technical rule, or concrete decision. The extraction incorrectly save |

## 5. 发现的问题清单

**P0 - 必须修复**：
- 无

**P1 - 建议修复**：
- 无

**P2 - 改进项**：
- 描述：本轮未发现硬失败；建议把 round4 作为后续重构固定回归门槛。
- 影响范围：可以防止冷启动、检索、自动沉淀路径在重构时静默退化。
- 复现步骤：后续主代码改动后重新运行 `python -m experiments.benchmarks.round4_regression.run_round4`。
- 涉及代码位置：experiments/benchmarks/round4_regression/run_round4.py


## 6. 回归基准

```yaml
regression_baseline:
  r1_onboarding:
    role_writeback: "5/5"
    tech_stack_writeback: "5/5"
    lesson_searchable: "5/5"
    zh_language_ok: "3/3"
    en_language_ok: "3/3"
    passed: true
  r2_user_context:
    identity_complete: "10/10"
    key_lesson_mentioned: "10/10"
    key_decision_mentioned: "10/10"
    key_knowledge_mentioned: "10/10"
    semantic_consistency_min: 1.00
    byte_identical: true
    passed: true
  r3_extraction:
    recall: "100.0%"
    precision: "83.3%"
    semantic_accuracy: "95.0%"
    false_positive: "2/5"
    passed: true
```

这些数字就是后续主代码重构后的对比门槛；下降项应优先解释或修复。

## 7. 修复验证（第四轮后续）

修复位置：
- src/piia_engram/core.py:1675-1687 (generate_context)
- src/piia_engram/core.py:1777-1789 (export_identity_card)

验证命令：
- `python -m experiments.benchmarks.round4_regression.run_round4`
- `python -m pytest -q experiments/benchmarks/round4_regression/test_identity_card_fix.py`
- `python -m experiments.benchmarks.round4_regression.test_identity_card_fix`

DeepSeek 证据：
- round4 全套：225 次调用，0 个 raw error，`results_raw.jsonl` 已保存 request/response/raw content。
- identity card 补充检查：3 次调用，3/3 正判，raw 保存到 `results_identity_card_fix_raw.jsonl`。

| 指标 | 修复前 | 修复后 | 是否回归通过 |
|------|--------|--------|------------|
| R1 role_writeback | 5/5 | 5/5 | 是 |
| R2 key_decision_mentioned | 0/10 | 10/10 | 是 |
| R2 byte_identical | true | true | 是 |
| R3 recall | 100% | 100.0% | 是 |
| R3 precision | 83.3% | 83.3% | 是 |
| export_identity_card 含 decisions | N/A | 3/3 | 是 |

补充说明：
- `export_identity_card()` 静态检查命中标题 `我的关键决策（请遵循）`，并命中 3/3 条 seed decision 的 question 或 choice 文本。
- DeepSeek 对 identity card 的语义判断为 3/3 正判，证据 quote 为：`身份卡是否应包含关键决策？ → 必须包含最近的关键决策，避免 AI 重复讨论已定事项`。

**最终判定**：修复成功。R2 已通过，R1/R3 无回归，identity card 的 decisions 导出也已通过静态与 DeepSeek 语义验证。
