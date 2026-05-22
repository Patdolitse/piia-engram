# Engram v3.14.3 — 评估证据包

**目的**：为外部 AI 评估者提供本轮所有需要查看的事实材料。本包内容**全部来自代码库实际状态**，不是宣传文案。

**评估对比基线**：v3.13.2（外部 5 家平均 6.9 分，最低维度"架构"5.4 分）→ v3.14.3。

**评估问题（核心）**：v3.13.2 评测发现的问题，v3.14.1/2/3 三连发是否充分修复？

---

## 一、版本变更摘要

### v3.14.1 (2026-05-22) — 重构 + 安全

- **`core.py` 4277 → 1083 行**（-74.7%），拆分为 7 个模块（Mixin 模式）：
  - `storage.py` (224) — 常量 + I/O 原语
  - `retrieval.py` (639) — `RetrievalMixin`: 搜索/评分/冲突/批量
  - `context.py` (688) — `ContextMixin`: `generate_context` + 注入/提取
  - `reconcile.py` (425) — `ReconcileMixin`: 外部 AI 记忆 + 配置同步
  - `reports.py` (1103) — `ReportsMixin`: 报告/身份卡/统计
  - `compat.py` (318) — OpenClaw/OCA 迁移
- **公开 API 不变** — 所有 `from piia_engram.core import X` 通过 re-export 继续工作
- **PBKDF2 100k → 600k 轮**（OWASP 2023+ 推荐底线），新前缀 `enc:v2:`
- **向后兼容**：`enc:v1:` 旧密文仍可解密
- **Schema 比较 bug 修复**：`_parse_schema_version` 转元组（避免 `"10.0" < "2.0"` 字典序）
- **22 处 `print(stderr)` → `logging.warning`**（跨 8 个模块）

### v3.14.2 (2026-05-22) — 测试 + 路径校验

- **测试 329 → 386**（+57 新增）
- **覆盖率基线 78%**（首次发布），8/12 模块 ≥85%
- 新文件 `tests/test_mcp_tools.py`（37 个测试）— MCP 工具 wrapper 直接覆盖
- 新文件 `tests/test_review_page_xss.py`（10 个测试）— `_esc` 转义验证
- 扩展 `tests/test_crypto.py`（+10）— v1↔v2 混合字段解密、v1→v2 升级、Unicode、坏 payload
- **新 `mcp_server._validate_path`**：拒绝路径参数里的 NUL 字节
  - 应用于 `import_engram` / `export_engram` / `save_project_snapshot`
- 配套 `.coveragerc` + `docs/coverage_baseline_v3.14.2.md`

### v3.14.3 (2026-05-22) — 文档 + 定位

- **`docs/architecture.md`** — 30 秒心智模型 + 完整模块图 + 三种数据流（冷启动/捕获/审核）+ 存储布局 + MCP 表面 + 贡献指南"在哪儿加东西"矩阵
- **`docs/comparison.md`** — 与 Letta、Mem0、Cline memories、Claude Code memory 的事实对比；显式标注"什么场景应该选别家"；identity-layer vs memory-layer 架构定位
- README 升级（中英双语）：
  - 对比表扩到 5 个竞品
  - 新"量化数据"段：43 个 MCP 工具、386 测试、78% 覆盖、PBKDF2 600k、<100ms 冷启动、0 网络调用
  - FAQ 新增 `piia-engram` PyPI 包名 vs "Engram" 产品名的品牌说明

---

## 二、v3.13.2 评估问题逐项核查

| # | v3.13.2 问题 | 决议 | v3.14.3 实际状态 |
|---|--------------|------|------------------|
| A | core.py 4277 行需拆分 | 接受 | ✅ 拆为 7 模块，core.py 1083 行 |
| B | `print(stderr)` 替换 logging | 接受 | ✅ 22 处全替换，每模块 `logger = logging.getLogger(__name__)` |
| C | `generate_review_page` 414 行 HTML 在数据层 | 接受 | ✅ 移到 `reports.py` 的 `ReportsMixin` |
| D | schema 字符串比较 bug | 接受(降级) | ✅ `_parse_schema_version` 元组比较 |
| E | 引入 Pydantic / dataclass | 暂缓 | ⏭ 维持现状（dict + 测试约定，未来再评估） |
| F | 异常层次结构 | 暂缓 | ⏭ 维持现状 |
| G | 常量迁移到 constants.py | 不接受 | ⏭ 改为 `storage.py` 集中（实际方案优于建议） |
| H | `_read_json` sys 未导入 NameError | 需验证 | ✅ 已修复（在 v3.14.0 一并处理） |
| I | PBKDF2 100k 偏低 | 接受 | ✅ 升到 600k，v1 仍可解密 |
| J | SECURITY.md 称 Fernet 实际是 AES-GCM | 接受 | ✅ 已更正为 AES-256-GCM（v3.14.0） |
| K | 时序攻击：SSE token 用 `==` 比较 | 接受 | ✅ 改为 `secrets.compare_digest`（v3.14.0） |
| L | `EncryptionEngine` 静默禁用 | 接受 | ✅ 改为 fail-fast `RuntimeError`（v3.14.0） |
| M | 0.0.0.0 绑定无 HTTPS 警告 | 接受 | ✅ SSE 加固 + `ENGRAM_CORS_ORIGINS`（v3.14.0） |
| N | 测试 327 不够 | 接受 | ✅ 升到 386（+59 vs v3.13.2） |
| O | 覆盖率未公开 | 接受 | ✅ 78% 基线发布 + `.coveragerc` |
| P | XSS 转义未测试 | 接受 | ✅ 10 个 XSS 测试覆盖 `_esc` |
| Q | 路径参数无校验 | 接受 | ✅ `_validate_path` NUL 字节防护 |
| R | 缺少架构文档 | 接受 | ✅ `docs/architecture.md` |
| S | 缺少竞品对比文档 | 接受 | ✅ `docs/comparison.md` |
| T | piia- 前缀引起品牌混乱 | 接受 | ✅ README FAQ 显式说明 |
| U | 缺少量化数据 | 接受 | ✅ README "By the numbers" 段 |

---

## 三、量化数据（v3.14.3）

| 指标 | v3.13.2 | v3.14.3 | 变化 |
|------|---------|---------|------|
| `core.py` 行数 | 4277 | 1083 | -74.7% |
| 模块数 | 6 | 13 | +7 |
| 测试数 | 327 | 386 | +59 |
| 覆盖率 | 未测量 | 78% | (首次发布) |
| PBKDF2 轮数 | 100,000 | 600,000 | 6x |
| MCP 工具 (Tier-1) | 10 | 10 | 不变 |
| MCP 工具 (全部) | 43 | 43 | 0（数字未变；v3.14.3 自评草稿一度误写 45，已在 v3.14.4 全局更正）|
| 文档页数 (docs/) | 1 (milestone) | 4 (+ architecture, comparison, coverage_baseline) | +3 |

---

## 四、评估材料清单

评估者应阅读以下材料形成判断：

### 4.1 必读

- `CHANGELOG.md`（v3.14.1/2/3 的 entries）
- `docs/architecture.md`（看是否真正解释清楚了拆分后的结构）
- `docs/comparison.md`（看竞品对比是否诚实）
- `docs/coverage_baseline_v3.14.2.md`（看覆盖率盲区是否诚实标注）
- `README.md`（量化数据段 + 品牌 FAQ）

### 4.2 代码核查（抽样）

- `src/piia_engram/core.py`（1083 行，看是否真正瘦身、向后兼容是否完整）
- `src/piia_engram/storage.py`（224 行，看是否真正成为单一 I/O 入口）
- `src/piia_engram/crypto.py`（看 PBKDF2 升级 + v1 兼容是否正确实现）
- `src/piia_engram/mcp_server.py`（前 200 行，看 `_validate_path`、`_apply_tool_tier` 等加固点）
- `tests/test_mcp_tools.py`（37 个，看测试是否真覆盖 MCP wrapper）
- `tests/test_review_page_xss.py`（10 个，看 XSS 测试是否到位）
- `tests/test_crypto.py`（19 个，看 v1↔v2 兼容测试）

---

## 五、评估维度（与 v3.13.2 一致，便于纵向对比）

| 维度 | v3.13.2 外部平均 | v3.13.2 自评 | v3.14.3 自评 | v3.14.3 外部 |
|------|------------------|--------------|--------------|--------------|
| 代码架构 | 5.4 | 7 | 8.5 | _请评估者填_ |
| 测试质量 | 7.2 | 7 | 8.5 | _请评估者填_ |
| 安全性 | 6.3 | 8 | 8.0 | _请评估者填_ |
| 文档完整性 | 7.7 | 8 | 9.0 | _请评估者填_ |
| 产品定位 | 7.1 | 7 | 8.0 | _请评估者填_ |
| **综合** | **6.9** | **7.5** | **8.4** | _请评估者填_ |

---

## 六、本轮评估的「关键提问」

评估者请重点回答（不要笼统打分，要给具体证据）：

1. **架构修复是否真正解决了 v3.13.2 的痛点？** core.py 从 4277 → 1083 行，但 Mixin 拆分是否引入新的复杂度（MRO、循环依赖、IDE 体验下降）？给出 1 个具体观察。

2. **测试覆盖率 78% 是否足够诚实？** 哪些模块覆盖率不到 70%（context.py 70%、setup_wizard.py 58%、mcp_server.py 54%）的解释是否合理？有没有应该补但没补的？

3. **PBKDF2 600k + v1 兼容方案是否正确？** 看 `crypto.py`：v2 的 prefix 切换、salt+nonce 编码、AESGCM 调用是否正确？有没有 timing 或 padding 漏洞？

4. **`_validate_path` 是否过于宽松？** 当前只拒绝 NUL 字节，没拒绝 `..` 或绝对路径。这个选择在 local-first 工具里合理吗？

5. **新文档（architecture.md / comparison.md）是否真正讲清楚了？** 还是营销话术？对照代码读，找一个具体的"文档错"或"文档对得很好"的点。

6. **新引入的潜在风险？** Mixin 多继承、`from .module import` 重导出层、新模块间的依赖关系，有什么 v3.13.2 没有的潜在问题？

7. **如果你是新用户，第一次读到 README，会被什么困惑？** 量化数据段、对比表、品牌 FAQ，哪一处仍可改进？

---

## 七、产出要求

每个评估者请输出：

1. **维度评分**（0-10）：架构 / 测试 / 安全 / 文档 / 定位 / 综合 — 必填
2. **逐问回答**（第六节 7 个问题）— 每个问题给具体引用
3. **三个最重要的新发现**（v3.13.2 评估没提过的）— 排序
4. **下一版本（v3.15.0 / v3.14.4）最该做的 3 件事**

不需要客套话。可以批评严厉。可以指出我们自评的盲区。
