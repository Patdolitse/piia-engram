# Engram v3.8.0 — Domain 多标签支持

## 核心改进

### Domain 多标签（软化）

- `_infer_domain()` 现在返回多个匹配域，逗号分隔（如 `"python,testing"`）
- `get_lessons(domain=...)` 从精确匹配改为包含匹配，查询 `"python"` 可命中 `"python,testing"` 的条目
- `get_relevant_lessons()` 分桶逻辑适配多标签
- 所有分析/报告中的 domain 聚合逻辑拆分多标签计数
- MCP 工具参数说明更新，标注支持多标签

### 向后兼容

- 已有单值 domain 条目完全不受影响
- 无数据库 schema 变更，增量改动最小化风险

## 验证

- **Round 7 T1 单元测试**：15/15（多标签推断 6/6、包含匹配 4/4、向后兼容 3/3、多标签计数 2/2）
- **Round 7 T2 回归测试**：19/20（与 Round 6 同场景基线一致，零回归）

## 完整变更

- feat: domain softening — support multi-label domains (50d0149)
- test: round 7 domain softening verification (1d1f64f)
