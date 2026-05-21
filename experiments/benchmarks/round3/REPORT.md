# MCP 协调器第三轮验证基准报告

## 1. 运行说明

- LLM：DeepSeek V3（`deepseek-chat`）
- 温度：0.0
- 每场景次数：3 次取多数
- 总调用次数：300
- 失败调用：0（0.0%）
- Usage：prompt=109551，completion=15897，total=125448 tokens
- 成本：本报告只记录 API usage，未按实时价格换算人民币；实际费用以 DeepSeek 控制台为准。

## 2. 测试 D 结果（真实 AI 显式传参）

| 指标 | 准确率 | 判定 |
|------|--------|------|
| Easy kind | 100.0% | ✅ |
| Medium kind | 100.0% | ✅ |
| Hard kind | 100.0% | ✅ |
| 整体 domain（含同义） | 54.3% | ❌ |

Hard 场景逐条结果：

| id | user_input | expected | actual | 正确 |
|----|------------|----------|--------|------|
| S07 | 用户决定取消订阅通常是 churn 的前置信号，不代表我们已经做了某个决策 | remember/lesson/product | remember/lesson/product_analytics | 否 |
| S08 | 学到了：API 限流策略选择不当会导致雪崩，不是要现在选择某个方案 | remember/lesson/backend | remember/lesson/system-design | 否 |
| S09 | 复盘：路径图谱里把安装位置写错，会让 AI 重复搜索浪费 token | remember/lesson/project_context | remember/lesson/general | 否 |
| S10 | 线上问题：测试框架名字写对了但命令路径错了，pytest 根本没跑到目标用例 | remember/lesson/testing | remember/lesson/python | 否 |
| S11 | 以后这个仓库都用 pytest，不再写 unittest 新用例 | remember/decision/testing | remember/decision/python | 否 |
| S12 | 这个项目的长期事实源固定为本地 JSON，云端只做可选备份 | remember/decision/architecture | remember/decision/architecture | 是 |
| S13 | 发布包统一从 mainline release pack 产出，维护包只做隔离验证 | remember/decision/release | remember/decision/release-engineering | 否 |
| C02 | 复盘一下，前端依赖管理工具选择不一致会让 CI 装出不同版本 | remember/lesson/javascript | remember/lesson/javascript | 是 |
| C04 | 学到一点：选择 npm 还是 pnpm 要统一，不然锁文件会互相打架 | remember/lesson/javascript | remember/lesson/javascript | 是 |
| C07 | 以后新增 Python 测试都走 pytest，不再扩 unittest | remember/decision/testing | remember/decision/python | 否 |
| C08 | 测试框架就定 pytest 了，unittest 只维护旧用例 | remember/decision/testing | remember/decision/python | 否 |
| C10 | 从今天起测试命令固定成 pytest，不再让多个框架并行 | remember/decision/testing | remember/decision/python | 否 |
| F03 | 学到一个后端事故规律：请求节流方案配置错了，会把下游服务一起拖垮 | remember/lesson/backend | remember/lesson/backend | 是 |
| F04 | 项目资产地图里如果把工具落盘目录记错，助手会反复重新定位，浪费上下文预算 | remember/lesson/project_context | remember/lesson/project-management | 否 |
| F05 | 线上复盘：测试入口字符串看似正确，但实际命令没有覆盖该跑的检查项 | remember/lesson/testing | remember/lesson/testing | 是 |
| F09 | Python 测试体系就收敛到一个轻量测试运行器，旧标准库框架只维护存量 | remember/decision/testing | remember/decision/python | 否 |
| F10 | 长期事实的主存储放在本机结构化文本里，远端同步只作为可选副本 | remember/decision/architecture | remember/decision/architecture | 是 |

## 3. 测试 C 结果（同义改写）

| 组 | 主题 | 同工具比例 | 判定 |
|----|------|-----------|------|
| decision_pytest | decision_pytest | 5/5 (100.0%) | ✅ |
| decision_stack_variant | decision_stack_variant | 5/5 (100.0%) | ✅ |
| lesson_incident_variant | lesson_incident_variant | 5/5 (100.0%) | ✅ |
| lesson_lockfile | lesson_lockfile | 5/5 (100.0%) | ✅ |
| recall_auth_variant | recall_auth_variant | 5/5 (100.0%) | ✅ |
| 平均 | | 100.0% | ✅ |

## 4. 测试 E 结果（37 工具近义）

| 组 | 准确率 |
|----|--------|
| lesson_decision_extract | 3/4 (75.0%) |
| search_relevant_similar | 3/3 (100.0%) |
| snapshot_context_user | 3/3 (100.0%) |
| 总体 | 9/10 (90.0%) |

判定：协调器是过度设计 ❌

## 5. 测试 F 结果（关键词泄露检测）

| 原场景 | 原版准确率 | 变体准确率 | 下降 |
|--------|----------|----------|------|
| S01 → F01 | ✅ | ✅ | +0.0pp |
| S02 → F02 | ✅ | ✅ | +0.0pp |
| S08 → F03 | ❌ | ✅ | -100.0pp |
| S09 → F04 | ❌ | ❌ | +0.0pp |
| S10 → F05 | ❌ | ✅ | -100.0pp |
| S04 → F06 | ✅ | ✅ | +0.0pp |
| S05 → F07 | ❌ | ❌ | +0.0pp |
| S06 → F08 | ✅ | ✅ | +0.0pp |
| S11 → F09 | ❌ | ❌ | +0.0pp |
| S12 → F10 | ✅ | ✅ | +0.0pp |
| S14 → F11 | ✅ | ✅ | +0.0pp |
| S15 → F12 | ✅ | ✅ | +0.0pp |
| S16 → F13 | ✅ | ✅ | +0.0pp |
| S17 → F14 | ✅ | ✅ | +0.0pp |
| E08 → F15 | ✅ | ✅ | +0.0pp |
| 平均 | 66.7% | 80.0% | -13.3pp |

判定：LLM 真懂语义 ✅

## 6. 与第二轮对比

| 测试 | 第二轮（rule） | 第三轮（DeepSeek） | 真实差距 |
|------|---------------|--------------------|---------|
| D hard kind | 100.0%（过拟合） | 100.0% | +0.0pp |
| D domain | 100.0%（过拟合） | 54.3% | -45.7pp |
| C 同义稳定性 | 100.0%（过拟合） | 100.0% | +0.0pp |
| E 37 工具 | 40.0% | 90.0% | +50.0pp |

## 7. 综合决策

### Q1：协调器 + AI 显式传参方案是否能上线？

Hard kind=100.0%，domain=54.3%。判定：hard kind ✅，domain ❌。

### Q2：协调器是否真的对同义改写更稳定？

5 组平均同工具稳定性为 100.0%，判定 ✅。

### Q3：37 工具是否真的不够用？

37 工具近义场景准确率 90.0%，判定：协调器是过度设计 ❌。

### Q4：AI 是真懂语义还是抓关键词？

关键词替换后准确率从 66.7% 到 80.0%，下降 -13.3pp，判定：LLM 真懂语义 ✅。

### 最终建议

方案 C：放弃协调器，保持 37 工具。D 不通过，说明显式传参或语义鲁棒性不足。

## 8. 局限说明

- DeepSeek V3 不是 Claude/Codex 本体，结论可能不完全代表实际使用环境。
- 65 个场景仍是有限样本。
- 本轮失败调用率为 0.0%；若失败率升高，需要复跑。
- 若进入实现，应再做端到端写入验证，检查实际存储内容是否符合预期。
