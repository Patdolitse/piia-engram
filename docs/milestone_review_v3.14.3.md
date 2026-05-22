# Engram v3.14.3 里程碑评估报告

**评估日期**：2026-05-22
**版本**：v3.14.3（v3.14.0 → v3.14.1 → v3.14.2 → v3.14.3 三连发后）
**评估方式**：DeepSeek-chat 4 passes（1 次 smoke + 3 次完整 run）
**对比基线**：v3.13.2（5 家外部 AI 评测，均分 6.9）→ v3.14.3
**底层数据**：[`experiments/evaluations/v3.14.3/REPORT.md`](../experiments/evaluations/v3.14.3/REPORT.md) · `results_<ts>.json` · `raw_log_<ts>.jsonl`

---

## 一、综合评分对比

| 维度 | v3.13.2 外部平均 (5 家) | v3.13.2 自评 | v3.14.3 自评 | v3.14.3 DeepSeek (4 pass 均) | Δ vs v3.13.2 外部 |
|------|--------------------------|--------------|--------------|------------------------------|-------------------|
| 代码架构     | 5.4 | 7   | 8.5 | **7.50** | **+2.10** ✅ |
| 测试质量     | 7.2 | 7   | 8.5 | **8.00** | +0.80 ✅ |
| 安全性       | 6.3 | 8   | 8.0 | **7.50** | +1.20 ✅ |
| 文档完整性   | 7.7 | 8   | 9.0 | **8.50** | +0.80 ✅ |
| 产品定位     | 7.1 | 7   | 8.0 | **8.00** | +0.90 ✅ |
| **综合**     | **6.9** | **7.5** | **8.4** | **7.90** | **+1.00** ✅ |

### 自评校准（v3.14.3 自评 vs DeepSeek）

| 维度 | 自评 | DeepSeek | 差距 | 判断 |
|------|------|----------|------|------|
| 架构 | 8.5 | 7.50 | -1.00 | 边缘高估（v3.13.2 是 +1.6，已收窄）|
| 测试 | 8.5 | 8.00 | -0.50 | 准确 |
| 安全 | 8.0 | 7.50 | -0.50 | 准确（v3.13.2 是 +1.7 显著盲区，已基本对齐）|
| 文档 | 9.0 | 8.50 | -0.50 | 准确 |
| 定位 | 8.0 | 8.00 | 0.00 | 完全准确 |

**结论**：自评校准从 v3.13.2 的 +1.6（架构）/ +1.7（安全）显著盲区，**收窄到本轮的 −0.5 到 −1.0 之间**，说明自评精度提升了。但仍系统性轻微高估，这是常见的"做的人比看的人对自己更宽容"。

### Pass 方差

| Pass | Arch | Test | Sec | Doc | Pos | Overall |
|------|------|------|-----|-----|-----|---------|
| 1 | 8 | 8 | 8 | 9 | 8 | 8.2 |
| 2 | 7 | 8 | 7 | 8 | 8 | 7.6 |
| 3 | 7 | 8 | 7 | 8 | 8 | 7.6 |
| 4 | 8 | 8 | 8 | 9 | 8 | 8.2 |

单 pass 方差 **0.6 分**（7.6 ↔ 8.2），证明跑多次取均值是必要的，不能信任单次评测。

---

## 二、v3.13.2 → v3.14.3 修复核查（21 项）

下表对应 `evidence_pack.md` 第二节的 21 项问题。

| # | v3.13.2 原问题 | 决议 | v3.14.3 实际动作 | DeepSeek verdict |
|---|----------------|------|------------------|-------------------|
| A | core.py 4277 行需拆分 | 接受 | 拆为 7 模块，core.py 1083 行 | **fixed** (4/4 pass 确认拆分到位，但 1/4 提出 reports.py 1103 行成新最大模块) |
| B | print(stderr) → logging | 接受 | 22 处替换 + 每模块 logger | **fixed** |
| C | generate_review_page HTML 在数据层 | 接受 | 移到 reports.py 的 ReportsMixin | **fixed**（但归到 reports.py 后变成新热点）|
| D | schema 字符串比较 bug | 接受(降级) | `_parse_schema_version` 元组比较 | **fixed** |
| E | 引入 Pydantic / dataclass | 暂缓 | 维持现状 | **unverified**（评测者认可暂缓）|
| F | 异常层次结构 | 暂缓 | 维持现状 | **partial**（v3.14.4 已加 `DecryptionError`）|
| G | 常量迁移到 constants.py | 不接受 | 改为 storage.py 集中 | **fixed**（评测者认为实际方案优于建议）|
| H | _read_json sys 未导入 | 需验证 | v3.14.0 修复 | **fixed** |
| I | PBKDF2 100k 偏低 | 接受 | 600k + enc:v2 前缀 + v1 兼容 | **fixed**（评测者验证实现正确，含 salt/nonce/AEAD）|
| J | SECURITY.md Fernet→AES-GCM | 接受 | v3.14.0 更正 | **fixed** |
| K | SSE token 时序攻击 | 接受 | `secrets.compare_digest` (v3.14.0) | **fixed** |
| L | EncryptionEngine 静默禁用 | 接受 | fail-fast RuntimeError (v3.14.0) | **fixed** |
| M | 0.0.0.0 绑定无警告 | 接受 | SSE 加固 + ENGRAM_CORS_ORIGINS (v3.14.0) | **fixed** |
| N | 测试 327 不够 | 接受 | 升到 386 (+59) | **fixed** |
| O | 覆盖率未公开 | 接受 | 78% 基线 + .coveragerc 发布 | **fixed**（评测者认可诚实标注，但指出 context.py 70% 的 LLM 分支缺失是真实风险）|
| P | XSS 转义未测试 | 接受 | 10 个 XSS 测试 | **fixed** |
| Q | 路径参数无校验 | 接受 | `_validate_path` NUL 字节防护 | **partial**（评测者认可设计合理，但提醒"未拒绝 .. 或绝对路径"）|
| R | 缺少架构文档 | 接受 | docs/architecture.md | **fixed**（评测者明确点名 "30 秒心智模型 + Where to add things 矩阵非常清晰"）|
| S | 缺少竞品对比文档 | 接受 | docs/comparison.md | **partial**（评测者指出 "Engram + Letta + Mem0 可一起用" 缺乏集成指导，略显营销）|
| T | piia- 前缀品牌混乱 | 接受 | README FAQ 显式说明 | **partial**（评测者：解释了包名但未说明为什么不直接用 engram）|
| U | 缺少量化数据 | 接受 | README "By the numbers" 段 | **partial→fixed (v3.14.4)**（评测者发现 README 45 vs 实际 43 矛盾，v3.14.4 已修）|

### 修复率统计

- **fixed**: 15 / 21（71%）
- **partial**: 5 / 21（24%）
- **unverified**: 1 / 21（5%，Pydantic 暂缓）
- **regression**: 0 / 21（0%）

---

## 三、DeepSeek 对 7 个关键问题的回答（汇总）

### Q1：架构修复是否真正解决 v3.13.2 痛点？引入了什么新复杂度？

**共识**（4/4 pass）：
- Mixin 拆分确实解决了 4277 行痛点
- 引入了 MRO 依赖：`Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)` 中，若两个 Mixin 定义同名方法（如 `_tokenize`），后继承的会覆盖前者。当前未发现冲突，但未来扩展时需小心。
- Re-export 层（core.py 从 storage.py re-export 常量）增加了间接性，IDE 跳转可能不准

**分歧**：1/4 pass 强烈指出 `reports.py` 1103 行成为新最大模块，建议下一步拆分；其他 pass 提到但优先级较低。

**我们的回应**：
- ✅ Mixin MRO 风险记入 v3.15.0 — 在 `docs/architecture.md` 加 MRO 章节 + 每个 Mixin 加 `# Requires: ...` docstring
- ✅ reports.py 拆分记入 v3.15.0 候选

### Q2：测试覆盖率 78% 是否足够诚实？

**共识**（4/4 pass）：基线诚实，盲区标注合理。

**关键发现**：
- `context.py` 70% 中的 `extract_knowledge` LLM 分支未测试 — 是**真实风险**，生产环境可能静默失败
- `setup_wizard.py` 58% 的交互流应优先补测（mock `input()`）
- `mcp_server.py` 54% — SSE 传输 + 约 18 个 wrapper 未测

**我们的回应**：
- ✅ v3.15.0 优先：context.py LLM 分支 mock + mcp_server.py SSE 集成测试

### Q3：PBKDF2 600k + v1 兼容方案是否正确？timing/padding 漏洞？

**共识**（4/4 pass）：实现正确。
- v2 使用 600k 迭代、16 字节随机 salt、12 字节 nonce、AESGCM.encrypt — 标准实现
- v1 兼容通过前缀识别并降级到 100k 迭代
- 测试覆盖了 v1→v2 升级、Unicode、坏 payload 等边界

**唯一新发现**：decrypt 失败时返回原始密文（不抛异常）**可能隐藏错误**。

**我们的回应**：
- ✅ **v3.14.4 已修** — 添加 `DecryptionError` + `strict=True` 参数，默认行为保留向后兼容，新代码推荐 `strict=True`
- ✅ `raise from None` 避免泄露 timing 信息

### Q4：`_validate_path` 是否过于宽松？

**共识**（4/4 pass）：在 local-first 工具中合理。
- 用户已有完整磁盘访问权限，过度限制会破坏合法用例（如 `~/.engram/` 外的导出路径）
- 但缺少对路径遍历的警告，可能被误用于意外覆盖文件

**我们的回应**：
- 维持当前实现。在 docstring 加注释说明这不是 sandboxing 边界

### Q5：新文档是否真讲清楚？

**正面**（多个 pass 主动赞扬）：
- `architecture.md` 的 "30 秒心智模型" 图和 "Where to add things" 矩阵**非常清晰**，直接对应代码结构
- README 量化数据段、对比表维度清晰

**负面**：
- `comparison.md` 中 "Engram + Letta + Mem0 可以一起用" 缺乏实际集成指导，略显营销
- 品牌 FAQ 解释了包名但未说明为什么用 `piia-` 前缀（评测者推测可能是 PyPI 名称冲突）

**我们的回应**：
- ✅ v3.15.0 candidate：comparison.md 补一节 "如何与 Letta/Mem0 并用"（哪些工具负责哪些数据）
- ⏭ piia- 前缀历史：FAQ 已说明"PIIA 体系前缀"，无更深技术原因

### Q6：Mixin 多继承 / 重导出层引入的新潜在风险？

**共识**（4/4 pass）：三类风险
1. **Mixin 多继承**：同名方法静默覆盖（如未来 ContextMixin 也定义 `_tokenize`）
2. **重导出层**：core.py 从 storage.py re-export 大量常量，若 storage 内部改名 re-export 可能不同步
3. **新模块依赖**：`context.py` 用 `TYPE_CHECKING` 避免循环导入，但运行时仍可能因延迟导入导致 `AttributeError`

**我们的回应**：
- ✅ v3.15.0：在 architecture.md 加 "Mixin 依赖矩阵" 表 + 每个 Mixin 显式声明 "I provide / I require" docstring
- ✅ v3.15.0：写一个 `test_mixin_integrity.py` — 静态扫描，确保没有同名方法跨 Mixin 定义

### Q7：新用户读 README 还会被什么困惑？

**主要共识**：
- **MCP 工具数量矛盾**（43 vs 45）— ⚠ 这是**我们的实际错误**，v3.14.4 已修
- 品牌 FAQ 解释了 `piia-engram` 但未说为什么用这个前缀

**我们的回应**：
- ✅ **v3.14.4 已修** README 中 MCP 工具数量统一为 43（实际数字）
- ✅ 所有相关文档（comparison.md、architecture.md、coverage_baseline）一并修正

---

## 四、新发现的问题（v3.13.2 没提过的）

### 🔴 HIGH — 已在 v3.14.4 修复或待 v3.14.4 修复

| 发现 | 提及次数 | 修复状态 |
|------|----------|---------|
| README/文档间 MCP 工具数量矛盾（45 vs 43） | 2/4 pass | ✅ **v3.14.4 已修**（统一为 43） |
| reports.py 1103 行成为新最大模块 | 1/4 pass HIGH + 1/4 MEDIUM | ⏭ v3.15.0 拆分 |
| Mixin 方法依赖隐式且脆弱 | 1/4 pass HIGH + Q6 4/4 共识 | ⏭ v3.15.0 显式化 |

### 🟡 MEDIUM

| 发现 | 提及次数 | 修复状态 |
|------|----------|---------|
| crypto.py decrypt 失败静默返回原密文 | 2/4 pass | ✅ **v3.14.4 已修**（加 `DecryptionError` + `strict`）|
| re-export 层增加间接性 / 可能循环导入 | 2/4 pass | ⏭ v3.15.0 文档说明 + 自动化测试 |
| `_apply_tool_tier` 测试只验证 noop | 1/4 pass | ⏭ v3.15.0 补测 |

### 🟢 LOW

| 发现 | 修复状态 |
|------|---------|
| storage.py 中 `_TERM_ALIASES` / `_ALIAS_LOOKUP` 被 re-export 为公开 API | 维持现状（实际上需要被 retrieval 引用） |
| `extract_knowledge` LLM 分支未测试 | ⏭ v3.15.0 mock 测试 |
| `comparison.md` 营销味的 "三家并用" 缺集成指导 | ⏭ v3.15.0 补集成示例 |

---

## 五、DeepSeek 建议的下一步优先级（4 pass 汇总）

去重后按出现频率排序：

| 优先级 | 建议 | 出现次数 | 我们取舍 |
|--------|------|----------|---------|
| 1 | 补测 context.py LLM + mcp_server.py SSE | 3/4 | ✅ **接受**，v3.15.0 |
| 2 | 修复 crypto.py 静默失败 | 2/4 | ✅ **已做**（v3.14.4）|
| 3 | 统一 README MCP 工具数量 | 2/4 | ✅ **已做**（v3.14.4）|
| 4 | 拆分 reports.py（1103 行）| 2/4 | ✅ **接受**，v3.15.0 |
| 5 | 显式化 Mixin 依赖 + MRO 文档 | 1/4 + Q6 共识 | ✅ **接受**，v3.15.0 |
| 6 | 完善品牌 FAQ（解释 piia- 前缀历史）| 1/4 | ⏭ **暂缓**（FAQ 已说明，进一步可能反而冗长）|
| 7 | architecture.md 加 "新增 identity field 需同步改 X、Y" | 1/4 | ✅ **接受**，v3.15.0 简短说明 |

---

## 六、纵向对比：v3.13.2 vs v3.14.3 的关键变化

### 6.1 数字层面

| 指标 | v3.13.2 | v3.14.3 (本次评测) | 变化 |
|------|---------|---------------------|------|
| 综合评分（外部）| 6.9 | 7.90 | +1.00 |
| 最高维度 | 文档 7.7 | 文档 8.50 | 维度未变，绝对值 +0.80 |
| 最低维度 | 架构 5.4 | 架构 / 安全 7.50（并列）| **架构 +2.10 (最大提升)** |
| 自评偏差最大维 | 安全 +1.7 | 架构 / 安全 −1.0（系统性轻微高估）| **盲区收窄 60%+** |
| 测试数 | 327 | 386 (v3.14.2) → 394 (v3.14.4) | +67 |
| 覆盖率 | 未测 | 78% | 首次发布 |
| core.py 行数 | 4277 | 1083 | −74.7% |
| 模块数 | 6 | 13 | +7 |
| MCP 工具数 | 43 | 43（未变）| 0 |

### 6.2 定性层面

**真正消失的痛点**：
- 架构 monolith 痛点（4277 行 → 7 模块）— 4/4 pass 都认可
- print(stderr) 协议污染 — 4/4 pass 认可
- HTML 拼接在数据层 — 4/4 pass 认可（但被搬到 reports.py 后变新热点）
- 没有架构文档 — 4/4 pass 主动赞扬 architecture.md
- 安全自评盲区 — 自评偏差从 +1.7 收窄到 −0.5

**从可见变隐性的**：
- reports.py 1103 行成为新的"4277 问题"
- Mixin 多继承的 MRO 风险（当前未发生，但需要主动文档化）
- Re-export 层的间接性（IDE 跳转 / 同步问题）

**v3.14.3 才暴露的新议题**：
- 自己写的量化数据矛盾（45 vs 43）— 已修
- crypto.py 静默失败问题 — 已修
- comparison.md 的 "三家并用" 缺集成指导

---

## 七、本轮评测方法学反思

**方法对比**：v3.13.2 用了 5 家 AI（Cursor / Claude Opus / Codex / GPT / DeepSeek），v3.14.3 只用了 DeepSeek（4 pass）。

### 单评测员的局限

- DeepSeek 的偏见：偏好严格的静态类型 / dataclass / Pydantic（在 Pass 间反复出现），与项目 "零依赖" 立场略有张力
- 没有 Claude / GPT 视角校验：可能漏掉前者擅长的"产品/定位/受众"视角问题
- 4 pass 方差 0.6 分，标准差约 0.3，说明取均值有效但不完全消除噪声

### 多 pass 的价值

- Pass 1 单独看可能被认为乐观（8.2），Pass 2/3 拉回 7.6 才是更稳的估值
- 单 pass 评测在未来评估中**不能作为最终结论**

### 下一次评测的建议

- **v3.15.0 评测时**：补回 GPT-5 / Claude Opus（如果可访问）做交叉验证
- **每次必跑 ≥ 3 pass**
- **跑前先 `engram doctor` 跑通**，避免脚本本身有 bug 浪费 API token
- 把 evidence_pack 加上"勘误"段，鼓励评测者指出我们自己写错的事实（这次的"43 vs 45"就是这样被抓到的）

---

## 八、下一版本（v3.14.4 / v3.15.0）行动清单

### v3.14.4（本评测后立即发布 — HIGH 项）

- ✅ 统一 MCP 工具数量为 43（README 中英 / comparison.md / architecture.md / coverage_baseline）
- ✅ `crypto.py` 加 `DecryptionError` + `strict=True` 参数（默认行为向后兼容；8 个新测试）
- ✅ 测试基线 394 passed

### v3.15.0（minor bump）候选

**架构 / 安全**：
1. 拆分 `reports.py`（1103 行 → 多个文件，类似 v3.14.1 的 core.py 重构）
2. 显式化 Mixin 依赖：每个 Mixin docstring 顶部写 "I provide / I require"
3. 新增 `tests/test_mixin_integrity.py` — 静态扫描跨 Mixin 同名方法冲突
4. `architecture.md` 加 MRO 章节 + "新增 identity field 同步清单"

**测试**：
5. `context.py` 中 `extract_knowledge` LLM 分支补 mock 测试
6. `mcp_server.py` SSE 传输集成测试（`test_mcp_e2e.py`）
7. `setup_wizard.py` 交互流补测（mock `input()`）

**文档**：
8. `comparison.md` 加一节 "如何与 Letta/Mem0 并用：实际集成步骤"

**评估**：
9. v3.15.0 release 后再跑一次 evaluation，**跨 ≥ 2 个 LLM**

---

## 附录

- 原始 DeepSeek 输出：`experiments/evaluations/v3.14.3/results_20260522_122523.json`
- 原始 API 调用日志：`raw_log_20260522_122523.jsonl` + `raw_log_20260522_122600.jsonl`（共 4 pass）
- 自动生成的简报：`experiments/evaluations/v3.14.3/REPORT.md`
- 证据包：`experiments/evaluations/v3.14.3/evidence_pack.md`
- 评测脚本：`experiments/evaluations/v3.14.3/run_evaluation.py`
- v3.13.2 评测基线：`docs/milestone_review_v3.13.2.md`

---

> **报告状态**：✅ 已完成（基于 4 pass DeepSeek 评测数据）
> **下一次评测**：v3.15.0 发布后，跨 ≥ 2 个 LLM 评测员
