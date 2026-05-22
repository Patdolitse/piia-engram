# Engram v3.16.0 — Cursor 独立评估

**评估时间**: 2026-05-22
**评估者**: Cursor

## 事实数据

- **测试**: 490 passed, 0 failed, 0 warnings（47.31s）
- **覆盖率**: 83% total（3765 stmts, 648 miss）；`mcp_server.py` **86%**（487 stmts, 70 miss）
- **源文件**: 18 个 `.py`（`src/piia_engram/`）
- **测试文件**: 11 个（`tests/`）
- **MCP 工具**: 43 个 `@mcp.tool()`（与 README 一致）
- **最大单文件**: `mcp_server.py` — **1411 行**
- **core.py**: **1097 行**（README 声称 1088，差 9 行，可接受舍入误差）
- **reports.py hub**: **21 行**（composition only，无业务逻辑）
- **reports 子模块**: `reports_rarity.py` 85 行、`reports_analytics.py` 418 行、`reports_review.py` 518 行、`reports_identity.py` 97 行
- **telemetry.py**: **312 行**（architecture.md 写 ~130，文档过时）
- **最低覆盖率模块**: `setup_wizard.py` **65%**（650 stmts, 228 miss）
- **README 量化声明**: 490 tests / 83% / 43 tools — **全部与 pytest 结果一致**

### 覆盖率分布（节选）

| 模块 | 覆盖率 |
|------|--------|
| reports.py | 100% |
| crypto.py | 97% |
| reports_review.py | 97% |
| reports_analytics.py | 95% |
| retrieval.py | 92% |
| telemetry.py | 92% |
| core.py | 86% |
| mcp_server.py | 86% |
| context.py | 70% |
| setup_wizard.py | 65% |

---

## 评分

| 维度 | 分数 | 理由（引用具体文件和行号）|
|------|------|--------------------------|
| 架构 | 8 | v3.16.0 reports 拆分到位：`reports.py:19-20` 仅组合 4 个 mixin，hub 21 行。`core.py:64` 的 `Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)` MRO 清晰，两层 mixin 嵌套（Reports → Rarity/Review/Identity/Analytics）合理，未过度抽象。扣分点：`mcp_server.py` 1411 行 + `core.py` 1097 行 + `setup_wizard.py` 993 行，三个近/超千行文件仍是导航成本；但这是 43 个 MCP wrapper 和用户 onboarding 的结构性结果，不是设计失误。 |
| 测试 | 7 | 490 测试全部通过，覆盖率 83% 不是刷出来的——核心域模块（core 86%、retrieval 92%、reports_review 97%）覆盖扎实。`test_mcp_coverage.py:40-103` 对 read/write 工具有 JSON 解析和错误路径断言，有实质内容。但 `test_mcp_coverage.py:68,84` 仅 `isinstance(parsed, (list, dict))`，回归防护力度弱。`setup_wizard.py` 65% 是最低覆盖区——这是用户首次接触的入口，风险与覆盖率不匹配。`test_review_page_xss.py` 和 `test_telemetry.py` 是加分项，说明安全路径有专门测试而非只靠 happy path。 |
| 安全 | 8 | Telemetry 三层防御：`telemetry.py:131-155` 长度上限 + 自然语言启发式 + 默认关闭；`telemetry.py:113-122` HMAC 日匿名 ID 不可跨天关联；Phase 1 无网络（`telemetry.py:201-215`）。HTML 注入：`reports_review.py:28` 自定义 `_esc` 覆盖 `&<>"'`，`reports_review.py:80-120` 所有用户字段经 `_esc` 输出；`test_review_page_xss.py` 有 10 个专项测试。远程模式：`mcp_server.py:175-179` TokenAuthMiddleware 用 Bearer token。路径：`mcp_server.py:127-149` 仅防 NUL/空值，注释诚实标注"非沙箱边界"。加密：PBKDF2 600k（README/architecture 一致）。未给 9 分因为 `_validate_path` 不防 `../` 遍历，虽对 local-first 合理，但 SSE 远程部署时依赖 token 而非路径隔离。 |
| 文档 | 6.5 | README 量化数据（490/83%/43）经验证全部准确——这在开源项目里少见。`architecture.md` 模块职责描述与代码流程一致（冷启动路径 `architecture.md:107-125` 与实际 mixin 调用匹配）。但多处数字过时：`architecture.md:79` 写 telemetry ~130 行，实际 312 行；`architecture.md:68` 写 reports_review ~520，实际 518（接近）；`architecture.md:77` 写 mcp_server ~1330，实际 1411。`CONTRIBUTING.md:28` 声称 "no telemetry" 与 `telemetry.py` 直接矛盾。`CONTRIBUTING.md:9-25` 架构概览仍描述 monolithic core，未反映 mixin 拆分，测试数标注也过时（如 test_core 188 仍对，但缺 test_mcp_coverage/test_telemetry 等新文件）。`architecture.md:255` 引用 `coverage_baseline_v3.14.2.md`（386 tests / 78%）已严重过时。 |
| 定位 | 8 | README 第 42-43 行与对比表（README:326-336）清晰区分"身份层"vs"agent memory"。Tier-1/Tier-2 分层（README:234-248, mcp_server.py:103-119）是产品级设计，降低 AI context 污染。staging→verified 晋升门（architecture.md:145-156）是真实差异化。扣分：「AI identity layer」作为品类仍需市场教育；investment analyst 等 use case（README:67-68）略 stretch。 |
| **综合** | **7.5** | 代码质量重构（reports 拆分、mcp 覆盖率从 v3.14.2 的 54% 到 86%）成效显著。安全设计和 README 诚实度超预期。主要拖累：CONTRIBUTING 与 architecture 数字维护滞后，setup_wizard 覆盖不足。 |

---

## vs DeepSeek（综合 7.53）

DeepSeek 给的分数：架构 8、测试 7、安全 8、文档 6.67、定位 8

| 维度 | DeepSeek | Cursor | 差异 |
|------|----------|--------|------|
| 架构 | 8 | 8 | 一致。完整代码确认 mixin 拆分不是表面功夫，reports hub 确实只有组合逻辑。 |
| 测试 | 7 | 7 | 一致。490 测试有实质，但 MCP 覆盖测试和 setup_wizard 是明确弱点。 |
| 安全 | 8 | 8 | 一致。telemetry payload 验证和 XSS 测试经代码确认有效。 |
| 文档 | 6.67 | 6.5 | 略低 0.17。DeepSeek 可能因截断材料低估了 README 量化准确性（这是加分项），但我读到 CONTRIBUTING 整段架构概览过时 + architecture.md telemetry 行数差 2 倍，扣分更重。 |
| 定位 | 8 | 8 | 一致。 |
| 综合 | 7.53 | 7.5 | 基本一致。我能运行完整 pytest 并读全文件，验证了 README 数字真实；文档维护滞后是独立确认的问题。 |

DeepSeek 的主要局限：无法运行测试验证 490/83%/43 声明，也无法看到 `test_review_page_xss.py` 等安全测试的存在。这些在我这边是加分项，但被 CONTRIBUTING/architecture 过时内容抵消。

---

## Top 3 问题

1. **CONTRIBUTING.md 与代码事实矛盾**（`CONTRIBUTING.md:28`）— 声称 "no telemetry"，但 `telemetry.py` 312 行已实现 opt-in 本地统计。新贡献者会被误导。修复成本：改一行，信任收益高。

2. **setup_wizard.py 覆盖率 65%，与用户首次体验风险不匹配** — 650 语句中 228 未覆盖（`setup_wizard.py:711-807, 817-868` 等大段交互逻辑）。doctor/setup/telemetry CLI 是用户 onboarding 主路径，当前测试集中在 happy path（`test_setup_wizard.py`），异常分支和 `--fix` 路径覆盖不足。

3. **architecture.md / CONTRIBUTING.md 数字维护滞后** — telemetry 行数差 2 倍、mcp_server 差 ~80 行、coverage baseline 仍指向 v3.14.2（386 tests / 78% / mcp_server 54%）。README 每次 release 更新数字，但 contributor 文档没跟上，形成"用户文档准确、贡献者文档过时"的分裂。

---

## Top 3 优点

1. **reports 拆分是教科书级 thin hub** — `reports.py:1-21` 从千行级模块变为纯组合，4 个子 mixin 职责单一（rarity 85 行、identity 97 行），零 API 破坏。`reports_rarity.py:22-80` 的评分逻辑自包含可读。

2. **Telemetry 安全设计超出 "Phase 1 local log" 的预期** — `telemetry.py:134-155` payload 验证器 + `test_telemetry.py:111-133` 专项测试，不是写了代码没测。默认关闭 + 无网络 + HMAC 日 ID 三层叠加，隐私 posture 诚实。

3. **README 量化声明可验证且准确** — 490 tests / 83% / 43 tools / core.py 1088 行，pytest 实测全部吻合（core 1097 行差 9 行可忽略）。大多数开源项目 README 数字是过期的；这里不是。

---

## 下一版本建议

1. **同步 CONTRIBUTING.md 设计原则** — 将 "no telemetry" 改为 "opt-in local telemetry only, no network by default"；更新 Architecture Overview 反映 mixin 结构和当前测试文件列表。

2. **setup_wizard 覆盖率目标 75%+** — 优先覆盖 `engram doctor --fix`、telemetry CLI 全分支、MCP 配置写入失败路径。用户首次体验不应是覆盖盲区。

3. **更新 architecture.md 行数表 + 新建 coverage_baseline_v3.16.0.md** — 每次 minor release 同步模块行数和覆盖率 baseline，避免 contributor 参考 v3.14.2 的 54% mcp_server 数字产生错误预期。
