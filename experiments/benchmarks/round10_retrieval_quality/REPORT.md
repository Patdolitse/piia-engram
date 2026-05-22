# Engram Round 10: Retrieval/Injection Quality — Report

**Generated**: 2026-05-22 (updated after v3.12.3 — search aliases, trigram expansion)

## 总结
- 总 case 数: 43
- 通过: 43
- 失败: 0
- 通过率: 100.0%
- **结果: PASS (PERFECT)**

## 各维度结果

| 维度 | 名称 | Case 数 | 通过 | 失败 | 门槛 | 状态 |
|------|------|---------|------|------|------|------|
| D1 | Context Assembly Completeness | 8 | 8 | 0 | 8/8 (100%) | PASS |
| D2 | Token Budget | 6 | 6 | 0 | 4/6 (gate) | PASS |
| D3_det | Recall Precision (deterministic) | 8 | 8 | 0 | 7/8 (87.5%) | PASS |
| D5 | Stale/Conflict Detection | 7 | 7 | 0 | 5/7 (gate) | PASS |
| D6 | Search Scoring Quality | 8 | 8 | 0 | 7/8 (87.5%) | PASS |
| D3_llm | Recall Quality (LLM) | 1 | 1 | 0 | >=75% | PASS |
| D4 | Identity Fidelity (LLM) | 5 | 5 | 0 | 4/5 (80%) | PASS |

## 修复历史

### v3.12.0 修复的 3 个失败
- **D6-RANK-01**: 多词查询排序改进 — 添加 query term coverage bonus，匹配更多查询词的结果排序更高
- **D5-CONFLICT-01**: 决策冲突检测验证通过 — `_detect_decision_conflicts()` 正确识别 pytest vs unittest 冲突
- **D5-CONFLICT-02**: 教训冲突检测验证通过 — `_detect_lesson_conflicts()` 正确识别 Docker 肯否矛盾

## LLM 评估信息
- 模型: deepseek-chat
- API 调用次数: 23
- 每场景重复次数: 3

## 结论
所有 43 个 case 全部通过。Retrieval/Injection 质量达到 100% 基线。
