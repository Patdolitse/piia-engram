# Engram v3.16.0 — 六方 AI 交叉评估汇总

**生成时间**: 2026-05-22
**评估者**: 6 家 AI（DeepSeek chat / DeepSeek v4-pro / Claude Code / ChatGPT / Cursor / Codex）

---

## 综合评分对比

| 维度 | DeepSeek chat | DeepSeek v4-pro | Claude Code | ChatGPT | Cursor | Codex | **六方均值** |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 架构 | 8.0 | 8.0 | 8 | 7.6 | 8 | 8 | **7.93** |
| 测试 | 7.0 | 7.67 | 7 | 7.4 | 7 | 8 | **7.35** |
| 安全 | 8.0 | 8.0 | 8 | 7.2 | 8 | 7 | **7.70** |
| 文档 | 6.67 | 7.67 | 7 | 6.9 | 6.5 | 7 | **6.96** |
| 定位 | 8.0 | 7.33 | 8 | 8.1 | 8 | 8 | **7.91** |
| **综合** | **7.53** | **7.53** | **7.6** | **7.5** | **7.5** | **7.6** | **7.54** |

## 评估条件对比

| 评估者 | 模型 | 能读完整代码 | 能跑测试 | Pass 数 |
|--------|------|:---:|:---:|:---:|
| DeepSeek chat | deepseek-chat | NO（截断材料） | NO | 3 |
| DeepSeek v4-pro | deepseek-v4-pro | NO（截断材料） | NO | 3 |
| Claude Code | claude-opus-4-6 | YES | YES | 1 |
| ChatGPT | GPT-5.5 Pro | NO（evidence_pack） | NO | 1 |
| Cursor | claude-sonnet-4-5 | YES | YES | 1 |
| Codex | codex-gpt-5 | YES | YES | 1 |

## 关键发现

### 六方共识（所有评估者都提到）

1. **reports.py 拆分是实质性改进** — 从 1103 行到 21 行 thin hub + 4 mixin，API 不变
2. **telemetry 安全设计超出预期** — 默认关闭、无网络、HMAC 日 ID、payload 验证
3. **README 量化声明可验证为真** — 490 / 83% / 43 全部精确匹配
4. **CONTRIBUTING.md "no telemetry" 与 telemetry.py 矛盾** — 6/6 评估者提及或验证
5. **文档是最弱维度** — 六方均值 6.96，contributor 文档维护明显滞后

### 分歧点

| 维度 | 高分方 | 低分方 | 分歧原因 |
|------|--------|--------|----------|
| 测试 | Codex (8) | DeepSeek chat / Claude / Cursor (7) | Codex 逐个检查了 test_mcp_coverage 的断言内容，认为有实质 |
| 安全 | DeepSeek / Claude / Cursor (8) | Codex (7) | Codex 发现 _validate_payload 不检查 dict key，认为安全边界比文档暗示的弱 |
| 文档 | DeepSeek v4-pro (7.67) | Cursor (6.5) | Cursor 发现 architecture.md telemetry 行数差 2 倍、CONTRIBUTING 架构概览过时 |
| 定位 | ChatGPT (8.1) | DeepSeek v4-pro (7.33) | v4-pro 对"同日发布多版本"印象负面；ChatGPT 更关注差异化定位本身 |

### 各评估者独有发现

**Codex（最深度安全审查）**：
- `_validate_payload` 只校验 dict value 不校验 key — 工具名作为 key 存在未来泄露风险
- `_track()` 只覆盖 Tier-1 工具子集，不能回答"43 个工具的使用分布"
- `wrap_up_session` 先 flush 后 track，顺序 bug 导致该工具自身不计入当日统计
- `docs/telemetry_roadmap.md` Phase 2 版本标注与实际不符
- `docs/comparison.md` 仍是 v3.14.2 数据（386 tests / 78%）

**ChatGPT**：evidence_pack 口径矛盾（最大文件 520 vs setup_wizard 650 行）

**Claude Code**：CONTRIBUTING.md:28 "no telemetry" 直接矛盾（首个发现者）

**DeepSeek v4-pro**：异常消息泄露内部实现细节 + telemetry.log 文件权限未限制

**Cursor**：architecture.md telemetry 行数写 ~130 实际 312（差 2 倍）、CONTRIBUTING 架构概览仍描述 monolithic core

## 可信度分析

| 评估者 | 可信度 | 原因 |
|--------|--------|------|
| Codex | **最高** | 读完整代码 + 跑测试 + 最深度安全审查（发现 payload key 盲区和 _track 覆盖不全） |
| Claude Code | **最高** | 读完整代码 + 跑测试，首个发现 CONTRIBUTING telemetry 矛盾 |
| Cursor | **最高** | 读完整代码 + 跑测试，发现 architecture.md 行数差 2 倍等深层文档问题 |
| DeepSeek v4-pro | 高 | 推理模型，3 pass 方差最小，发现了安全细节 |
| DeepSeek chat | 中高 | 与 v4-pro 结论基本一致，分析深度略浅 |
| ChatGPT | 中 | 发现 evidence_pack 口径矛盾（有价值），但多维度因"无法验证"给保守分 |

## 综合结论

**v3.16.0 综合评分：7.54（六方均值）**

- 六方评估高度一致（7.5-7.6 区间，极差仅 0.1）
- 架构和定位是最强维度（均值 7.91-7.93）
- 文档是最弱维度（均值 6.96），主要扣分项：CONTRIBUTING "no telemetry" 矛盾、architecture.md 行数过时、comparison.md / telemetry_roadmap.md 未同步
- 项目处于**良好状态**，无阻塞性问题，有明确改进路径

## 下一步行动项（六方建议汇总）

按被提及次数和严重度排序：

| 优先级 | 行动项 | 提及次数 | 来源 |
|--------|--------|:---:|------|
| 1 | 修复文档矛盾（CONTRIBUTING "no telemetry" + architecture.md 行数 + comparison.md 数据 + telemetry_roadmap Phase 标注） | **6/6** | 全部 |
| 2 | telemetry _validate_payload 增加 key 校验 + 工具名白名单 | 1/6 | Codex（深度安全审查） |
| 3 | _track() 覆盖全部 43 个工具 + 修复 wrap_up_session flush 顺序 | 1/6 | Codex |
| 4 | HTML 模板分离（reports_review.py） | 4/6 | DeepSeek×2, Claude, ChatGPT |
| 5 | setup_wizard.py 覆盖率 65% → 75%+ | 3/6 | Claude, Cursor, Codex |
| 6 | 加强 benchmark 门槛和透明度 | 3/6 | DeepSeek v4-pro×3 pass |
| 7 | 新建 coverage_baseline_v3.16.0.md | 2/6 | Cursor, Codex |
| 8 | 异常消息脱敏（mcp_server.py） | 1/6 | DeepSeek v4-pro |
| 9 | telemetry.log 文件权限 | 1/6 | DeepSeek v4-pro |

---

## 文件清单

```
experiments/evaluations/v3.16.0/
    evidence_pack.md              — 评估证据包
    run_evaluation.py             — DeepSeek API 评测脚本
    REPORT.md                     — DeepSeek v4-pro 3-pass 报告
    results_20260522_170255.json  — v4-pro 原始结果
    raw_log_20260522_170255.jsonl — v4-pro 原始日志
    cross_ai/
        facts.json                — Codex 事实数据（覆写了 Claude Code 版本）
        scores.json               — Codex 评分（覆写了 Claude Code 版本）
        REPORT.md                 — Codex 完整报告（覆写了 Claude Code 版本）
        chatgpt_scores.json       — ChatGPT GPT-5.5 Pro 评分
        cursor_report.md          — Cursor 完整报告
        SUMMARY.md                — 本文件（六方汇总）
```

注：Claude Code 的评估数据（facts/scores/REPORT）被 Codex 覆写（两者输出路径相同）。
Claude Code 的评分已保留在本 SUMMARY 中：架构 8 / 测试 7 / 安全 8 / 文档 7 / 定位 8 / 综合 7.6。
