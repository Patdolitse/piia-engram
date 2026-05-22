# Engram v3.16.0 — 评估证据包

**目的**：为外部 AI 评估者提供本轮所有需要查看的事实材料。本包内容**全部来自代码库实际状态**，不是宣传文案。

**评估对比基线**：v3.14.3（DeepSeek 4-pass 平均 7.90 分）→ v3.16.0。

**核心评估问题**：v3.14.3 评测时的建议和发现，在 v3.15.0 → v3.15.1 → v3.16.0 三个版本中是否被充分处理？新增功能是否引入新风险？

---

## 一、版本变更摘要

### v3.15.0 (2026-05-22) — 隐私 + 遥测 + 质量基线

- **匿名使用统计（Phase 1: 纯本地日志）** — `telemetry.py` 模块
  - 默认关闭，需在 `engram setup` 第 5 步或 `engram telemetry on` 显式开启
  - 仅收集 4 个字段：工具调用分布（成功/失败计数）、知识条目总数、engram 版本、日匿名 ID
  - 日 ID 用 `HMAC(local_uuid, date)` — 无法跨天关联
  - Payload 验证器拒绝 >200 字符或含自然语言模式的字符串（防止内容泄露）
  - 所有数据存 `~/.engram/telemetry.log`（JSONL，人类可读）
  - **无网络请求** — Phase 2 需满足 30 天 + 5 用户
  - CLI: `engram telemetry status|preview|on|off`
- **Reconcile 授权门** — 需显式授权才能同步外部 AI 记忆
- **Setup 向导第 5 步: 隐私偏好** — 数字选择 UI
- **ToolCallTracker 接入 MCP Server** — 10 个 Tier-1 工具接入成功/失败追踪
- **Round 10 召回/注入质量基准测试** — 43 case 全过，覆盖 6 个维度
- **测试 394 → 437**（+43）

### v3.15.1 (2026-05-22) — GBK 修复 + README 优化

- `_safe_print()` 修复 Windows 中文 GBK 控制台 emoji 崩溃
- README 添加 PyPI 下载 badge、"30 秒"快速开始、CLI 命令参考

### v3.16.0 (2026-05-22) — 代码质量 + 覆盖率

- **reports.py 拆分**: 1103 行 → 5 个模块（Mixin 模式）
  - `reports_rarity.py` (85 行) — RarityMixin: RARITY_TIERS + classify_rarity
  - `reports_review.py` (520 行) — ReviewMixin: HTML 审查页、promote/archive
  - `reports_identity.py` (97 行) — IdentityCardMixin: Markdown 身份卡导出
  - `reports_analytics.py` (310 行) — AnalyticsMixin: 健康报告/过期/摘要/统计
  - `reports.py` (22 行) — 薄 hub，组合 4 个子 mixin
- **mcp_server.py 测试覆盖率 58% → 86%**（+53 个新测试）
- **测试总数 437 → 490**（+53）
- **最大单文件**：从 1103 行降到 520 行（reports_review.py）

---

## 二、v3.14.3 评测建议逐项核查

| # | v3.14.3 评测建议 | v3.16.0 状态 |
|---|------------------|--------------|
| 1 | reports.py 1103 行仍需拆分 | ✅ 拆为 5 模块，最大 520 行 |
| 2 | mcp_server.py 覆盖率偏低（54-58%）| ✅ 升到 86% |
| 3 | 增加更多集成测试 | ✅ +53 个 MCP wrapper 测试 |
| 4 | 遥测设计安全（不泄露内容）| ✅ Payload 验证器 + 本地日志 |
| 5 | 冷启动质量可量化验证 | ✅ Round 10 基准测试 43/43 通过 |

---

## 三、量化数据（v3.16.0）

| 指标 | v3.14.3 | v3.16.0 | 变化 |
|------|---------|---------|------|
| 测试数量 | 394 | 490 | +96 (+24%) |
| 总覆盖率 | 78% | 83% | +5pp |
| mcp_server 覆盖率 | 54% | 86% | +32pp |
| core.py 行数 | 1083 | 1088 | 无显著变化 |
| reports.py 行数 | 1103 | 22（hub）| -98% |
| 最大单文件 | 1103 (reports.py) | 650 (setup_wizard.py) | -41% |
| 源代码模块数 | 12 | 16 | +4（reports 子模块）|
| MCP 工具数 | 43 | 43 | 无变化 |
| 基准测试 case | 0 | 43（Round 10）| 新增 |
| PyPI 版本 | 3.14.4 | 3.16.0 | 跨 5 个版本 |

---

## 四、架构变化分析

### 新 Mixin 层级

```
Engram
├── RetrievalMixin (retrieval.py)
├── ContextMixin (context.py)
├── ReconcileMixin (reconcile.py)
└── ReportsMixin (reports.py — thin hub)
    ├── RarityMixin (reports_rarity.py)
    ├── ReviewMixin (reports_review.py)
    ├── IdentityCardMixin (reports_identity.py)
    └── AnalyticsMixin (reports_analytics.py)
```

**关键变化**：reports.py 从一个 1103 行的单体 mixin 变成 4 个子 mixin 的组合 hub（22 行）。公开 API 完全不变 — `from piia_engram.reports import ReportsMixin` 继续工作。

### 新模块文件（v3.15.0+）

| 模块 | 用途 | 行数 |
|------|------|------|
| telemetry.py | 匿名使用统计 | ~130 |
| stats.py | CLI 统计命令 | ~99 |
| reports_rarity.py | 品质分类 | ~85 |
| reports_review.py | HTML 审查页 | ~520 |
| reports_identity.py | 身份卡导出 | ~97 |
| reports_analytics.py | 分析报告 | ~310 |

---

## 五、安全审计点

1. **telemetry.py Payload 验证**：
   - 字符串字段 >200 字符 → 拒绝
   - 含空格的自然语言模式 → 拒绝（防止内容泄露）
   - 嵌套层级 >2 → 拒绝
   - 日 ID 用 HMAC + 每日轮换 → 不可跨天追踪

2. **_safe_print()** 安全性：
   - 使用 `sys.stdout.encoding` + `errors="ignore"` 降级
   - 不会静默截断安全相关输出（仅影响装饰性 emoji）

3. **新模块无新攻击面**：
   - reports_*.py 拆分仅涉及代码移动，无新的 I/O 路径
   - 所有 HTML 输出仍通过 `_esc()` 转义

---

## 六、评估问题

1. **架构复杂度**：16 个源文件（从 12 个增加）是否过度？Mixin 两层嵌套（ReportsMixin → 4 子 mixin）是否增加了理解成本？

2. **测试覆盖率真实性**：490 个测试、83% 覆盖率——是否有过多的浅层"调用一下就算覆盖"的测试？关键路径是否真的被充分验证？

3. **遥测安全**：telemetry.py 的 payload 验证器是否足够防止内容泄露？本地日志是否有意外暴露敏感数据的风险？

4. **Round 10 基准测试**：43 个 case 100% 通过——测试是否过于宽松？通过门槛是否合理？

5. **文档维护**：4 个新模块是否在 architecture.md 中得到更新？CONTRIBUTING.md 的测试基线是否更新？

6. **新风险**：reports_review.py 520 行 HTML 模板仍在 Python 中——是否应该考虑模板引擎？

7. **产品定位一致性**：从 v3.14.3 到 v3.16.0 跳了多个版本号——版本策略是否清晰？
