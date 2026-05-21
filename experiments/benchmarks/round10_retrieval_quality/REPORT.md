# Engram Round 10: Retrieval/Injection Quality — Report

**Generated**: 2026-05-22 02:19

## 总结
- 总 case 数: 43
- 通过: 40
- 失败: 3
- 通过率: 93.0%
- **结果: PASS**

## 各维度结果

| 维度 | 名称 | Case 数 | 通过 | 失败 | 门槛 | 状态 |
|------|------|---------|------|------|------|------|
| D1 | Context Assembly Completeness | 8 | 8 | 0 | 8/8 (100%) | PASS |
| D2 | Token Budget | 6 | 6 | 0 | 4/6 (gate) | PASS |
| D3_det | Recall Precision (deterministic) | 8 | 8 | 0 | 7/8 (87.5%) | PASS |
| D5 | Stale/Conflict Detection | 7 | 5 | 2 | 5/7 (gate) | PASS |
| D6 | Search Scoring Quality | 8 | 7 | 1 | 7/8 (87.5%) | PASS |
| D3_llm | Recall Quality (LLM) | 1 | 1 | 0 | >=75% | PASS |
| D4 | Identity Fidelity (LLM) | 5 | 5 | 0 | 4/5 (80%) | PASS |

## 失败详情

- **D6-RANK-01**: pytest_in_top5=1, total_results=10

## 已知问题（预期失败）

- **D5-CONFLICT-01**: pytest=True, unittest=True, conflict_warning=False → 冲突检测尚未实现
- **D5-CONFLICT-02**: conflict_warning=False → 冲突检测尚未实现

## LLM 评估信息
- 模型: deepseek-chat
- API 调用次数: 23
- 每场景重复次数: 3

## 结论
所有 HARD GATE 和 SOFT GATE 均达标。Retrieval/Injection 质量基线已建立。
