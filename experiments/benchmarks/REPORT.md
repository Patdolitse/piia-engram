# MCP 工具选择准确率基准测试报告

## 运行说明

- 评估代理：rule_based
- 真实 LLM 可用：False
- LLM 不可用原因：OPENAI_API_KEY and ANTHROPIC_API_KEY are not set
- 说明：真实 LLM API 当前不可用，使用透明规则代理。该结果不是 Claude/Cursor 的实测行为。
- 原子工具数：37
- 协调器工具数：5（advanced 正确行为单独记为 fallback/router）

## 1. 数据对比表

| 指标 | 37 工具 | 5 协调器/回退 | 差异 |
|------|--------|---------------|------|
| 总体准确率 | 100.0% | 100.0% | +0.0pp |
| Easy 场景 | 100.0% | 100.0% | +0.0pp |
| Medium 场景 | 100.0% | 100.0% | +0.0pp |
| Hard 场景 | 100.0% | 100.0% | +0.0pp |
| Advanced 场景 | 100.0% | 100.0% | +0.0pp |

补充：这里把 advanced 的正确处理记为 fallback/router，因为任务包明确要求观察 5 个协调器覆盖不到的场景。如果产品形态强制只暴露 `remember/recall/cleanup/inherit/sync` 且没有回退层，advanced 场景准确率应视为 0%。

## 2. 协调器内部判断准确率

| 判断 | 准确率 | 主要错误模式 |
|------|--------|------------|
| lesson vs decision | 61.5% | lesson 被关键词误判为 decision：2；无关键词 decision 被判 lesson：3 |
| domain 推断 | 15.4% | 误判输出集中在 general=10，project_context=1 |

- Hard 场景下 kind 误判率：71.4%
- remember 类场景数量：13

## 3. 错误案例分析

### S07：内部 kind 误判

- 场景内容：用户决定取消订阅通常是 churn 的前置信号，不代表我们已经做了某个决策
- 期望：kind=lesson
- 实际：kind=decision
- 错误原因分析：原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。

### S08：内部 kind 误判

- 场景内容：学到了：API 限流策略选择不当会导致雪崩，不是要现在选择某个方案
- 期望：kind=lesson
- 实际：kind=decision
- 错误原因分析：原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。

### S11：内部 kind 误判

- 场景内容：以后这个仓库都用 pytest，不再写 unittest 新用例
- 期望：kind=decision
- 实际：kind=lesson
- 错误原因分析：原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。

### S12：内部 kind 误判

- 场景内容：这个项目的长期事实源固定为本地 JSON，云端只做可选备份
- 期望：kind=decision
- 实际：kind=lesson
- 错误原因分析：原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。

### S13：内部 kind 误判

- 场景内容：发布包统一从 mainline release pack 产出，维护包只做隔离验证
- 期望：kind=decision
- 实际：kind=lesson
- 错误原因分析：原型只要看到“decided/choose/选择/决定/决策”等关键词就判为 decision，缺少语义判断。

### S01：内部 domain 误判

- 场景内容：刚发现 Windows 上 Python 默认用 GBK 编码会让 twine 上传失败
- 期望：domain=python
- 实际：domain=general
- 错误原因分析：infer_domain 目前只识别 mcp/tool/工具 与 project/asset/路径/图谱，覆盖面过窄。

### S02：内部 domain 误判

- 场景内容：踩坑：npm 和 pnpm 混用会让锁文件漂移，CI 里依赖版本对不上
- 期望：domain=javascript
- 实际：domain=general
- 错误原因分析：infer_domain 目前只识别 mcp/tool/工具 与 project/asset/路径/图谱，覆盖面过窄。

## 4. 结论与建议

### Q1：协调器是否真的提升了 AI 选择准确率？

在本次规则代理数据里，37 工具与 5 协调器/回退的工具选择准确率相同，都是 100.0%。因此没有数据证明协调器单靠“减少工具数量”能带来显著提升；在没有真实 LLM 实测前，不能把协调器收益说成已被证明。

### Q2：协调器的内部判断是否够用？

不够。当前关键词分类的 kind 准确率只有 61.5%，domain 准确率只有 15.4%，kind 误判率已经高于 15% 阈值。尤其是含“决定/选择”但本质是 lesson 的陷阱，以及没有关键词但本质是 decision 的表达，会稳定误判。

### Q3：advanced 场景如何处理？

必须保留 advanced tier 或显式 fallback/router。导出、审计日志、手动合并、身份配置更新都不是五个高层工具能自然承载的动作；如果强行塞进 `cleanup` 或 `recall`，会把 schema 清晰度重新变成隐式路由问题。

### Q4：综合建议

推荐方案 C 的收缩版：可以继续探索协调器，但不能只做 5 个工具。更稳妥的产品形态是：

- 默认层保留 `remember/recall/cleanup/inherit/sync`，降低普通任务的选择负担。
- `remember` 必须让 AI 显式传 `kind` 和 `domain`，不要依赖当前关键词推断。
- 保留 advanced tier/fallback，覆盖 export、audit、merge、identity update 等维护动作。
- 在真实 LLM API 可用后复跑本基准，每个场景 3 次取多数，再决定是否替代现有 37 工具。

当前数据不支持直接替换为纯 5 工具的强结论；支持的结论是：协调器可以继续作为默认使用层探索，但原型内部判断必须重做，advanced 不能消失。
