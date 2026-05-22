# Engram Round 10: Retrieval/Injection Quality — Report

**Generated**: 2026-05-22 20:36

## 总结
- 总 case 数: 43
- 通过: 43
- 失败: 0
- 通过率: 100.0%
- **结果: PASS**

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

## LLM 评估信息
- 模型: deepseek-v4-pro
- API 调用次数: 23
- 每场景重复次数: 3

## 结论
所有 HARD GATE 和 SOFT GATE 均达标。Retrieval/Injection 质量基线已建立。
