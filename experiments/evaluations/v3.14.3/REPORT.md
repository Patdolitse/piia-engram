# Engram v3.14.3 — DeepSeek Milestone Evaluation

**Run timestamp**: 20260522_122600
**Evaluator**: deepseek-chat (3 passes)

## Average scores

| Dimension | v3.13.2 (5-evaluator avg) | v3.14.3 (this run) |
|-----------|----------------------------|--------------------|
| architecture | 5.4 | **7.33** |
| testing | 7.2 | **8.0** |
| security | 6.3 | **7.33** |
| documentation | 7.7 | **8.33** |
| positioning | 7.1 | **8.0** |
| overall | 6.9 | **7.8** |

## Per-pass detail

### Pass 1

**Scores**: {"architecture": 7, "testing": 8, "security": 7, "documentation": 8, "positioning": 8, "overall": 7.6}

**Key Q&A**:
- *q1_architecture_complexity*: Mixin 拆分确实解决了 core.py 臃肿问题，但引入了 MRO 依赖：Engram 类继承顺序为 RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin，若两个 Mixin 定义了同名方法（如 _tokenize 在 retrieval.py 中定义，但 context.py 未覆盖），则 MRO 决定调用哪个。当前未发现冲突，但未来扩展时需小心。此外，re-export 层（core.py 从 storage.py 等导入再导出）增加了间接性，IDE 跳转可能不准确。
- *q2_coverage_honesty*: 78% 覆盖率诚实标注了盲区。mcp_server.py 54% 和 setup_wizard.py 58% 的解释合理（SSE 传输、交互式输入难以单元测试）。但 context.py 70% 的盲区包括 extract_knowledge LLM 分支，该分支是核心功能，应通过 mock 测试覆盖。另外，reports.py 91% 看似高，但 generate_review_page 的 HTML 生成逻辑复杂，缺少对罕见分支（如空数据、异常 rarity 分类）的测试。
- *q3_pbkdf2_correctness*: crypto.py 实现正确：v2 使用 600k 迭代，v1 使用 100k 迭代，decrypt 根据前缀选择迭代次数。encrypt 对已加密值（v1 或 v2）返回原值，避免双重加密。salt 和 nonce 随机生成，每个值不同。AESGCM 调用正确（nonce 12 字节，关联数据为 None）。潜在问题：decrypt 失败时返回原值（日志警告），但攻击者可利用此行为进行 padding oracle 类攻击？AES-GCM 是认证加密，失败时不会泄露明文，但返回原值可能误导调用方。
- *q4_path_validation*: _validate_path 只拒绝 NUL 字节，不拒绝 .. 或绝对路径，在 local-first 场景下合理：用户已有完整磁盘访问权限，路径遍历不是威胁模型。但拒绝 NUL 字节是必要的，因为 C 级 API 会静默截断。当前实现足够。
- *q5_doc_clarity*: architecture.md 的 '30-second mental model' 图清晰，模块职责表准确。但 'Where to add things' 矩阵中，'A new identity field' 指向 core.py 和 storage.py，但实际新增字段需同时修改 _ALLOWED_PROFILE_FIELDS（storage.py）和访问器（core.py），文档未说明需同步更新两个位置。comparison.md 诚实标注了 Engram 不做的事（无向量嵌入、无云存储、无自动记忆编辑），并明确 'choose someone else when' 场景，是高质量竞品文档。
- *q6_new_risks*: Mixin 多继承：若两个 Mixin 定义了相同方法签名但不同行为，MRO 可能导致意外调用。例如 retrieval.py 定义了 _tokenize，若未来 context.py 也定义 _tokenize，则调用顺序取决于继承顺序。重导出层：core.py 从 storage.py 导入所有常量再导出，但 storage.py 中 _TERM_ALIASES 等私有常量也被导出，可能被外部误用。新模块依赖：reconcile.py 依赖 retrieval.py 的 _bigram_similarity，但未显式声明依赖，若 retrieval.py 重构可能静默破坏。
- *q7_readme_confusion*: 量化数据段中 '45 个 MCP 工具' 与证据包三节 'MCP 工具 (全部) 45' 一致，但 README 前文称 '43 MCP 工具'（在 'MCP Tools' 段），存在矛盾。品牌 FAQ 解释了 piia-engram 包名，但未说明为何不直接用 engram（可能 PyPI 名称冲突），用户可能困惑。对比表维度清晰，但 'Knowledge tiers' 列 Engram 标注 ✅，而 Mem0 标注 ❌，但 Mem0 有 'staging' 概念（通过 metadata），对比不够精确。

**New findings**:
- [high] **README 中 MCP 工具数量矛盾** — README 'MCP Tools' 段称 '43 MCP 工具'，但 'By the numbers' 段称 '45 个 MCP 工具'。证据包三节显示 v3.14.3 全部工具为 45，v3.13.2 为 43。README 未更新旧数字，造成不一致。
- [medium] **re-export 层可能引入循环导入** — core.py 从 retrieval.py、context.py 等导入 Mixin 类，而 retrieval.py 等又通过 TYPE_CHECKING 从 core.py 导入 Engram 类型。当前使用 TYPE_CHECKING 避免运行时循环，但若未来在非类型检查上下文中导入，可能触发循环。
- [low] **storage.py 中 _TERM_ALIASES 和 _ALIAS_LOOKUP 被 re-export 为公开 API** — core.py 通过 from .storage import _TERM_ALIASES, _ALIAS_LOOKUP 导出这些以下划线开头的私有常量。外部代码可能依赖它们，但按 Python 惯例下划线表示内部实现，未来改动可能破坏外部代码。

**Suggested next 3**:
1. **修复 README 中 MCP 工具数量矛盾** — 用户首次阅读时发现矛盾数字会降低信任度。应统一为 45，并确保 'MCP Tools' 段和 'By the numbers' 段一致。
2. **为 context.py 中 extract_knowledge LLM 分支添加 mock 测试** — 该分支覆盖率 70% 的盲区，是核心功能（LLM 驱动提取），应通过 mock 外部依赖来测试，避免回归。
3. **在 architecture.md 中明确新增 identity field 需同步修改 storage.py 和 core.py** — 当前 'Where to add things' 矩阵指向 core.py 和 storage.py，但未说明需同时修改两个文件，可能导致遗漏。

**Evaluator's own uncertainty**: 最大不确定性：未实际运行测试和覆盖率工具，仅凭代码和文档评估，可能遗漏运行时错误或测试假阳性。

---

### Pass 2

**Scores**: {"architecture": 7, "testing": 8, "security": 7, "documentation": 8, "positioning": 8, "overall": 7.6}

**Key Q&A**:
- *q1_architecture_complexity*: Mixin 拆分确实解决了 core.py 行数问题，但引入了 MRO 依赖：Engram 类定义 class Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)，Mixin 顺序影响方法解析。例如，若两个 Mixin 定义了同名方法（如 _tokenize），后一个会覆盖前一个。当前代码中 retrieval.py 定义了 _tokenize，其他 Mixin 未定义，但未来扩展时需小心。此外，re-export 层（core.py 从 storage.py 等 re-export 常量）增加了间接性，IDE 跳转可能不准确。
- *q2_coverage_honesty*: 78% 覆盖率基线诚实标注了盲区：mcp_server.py 54%（SSE 传输 + 未测试的 tool wrapper）和 setup_wizard.py 58%（交互式流程）。解释合理——SSE 需要集成测试，交互式需要 mock。但 context.py 70% 的盲区（extract_knowledge LLM 分支）应优先补测，因为 LLM 分支是核心功能，且无测试覆盖可能隐藏回归。
- *q3_pbkdf2_correctness*: crypto.py 实现正确：v2 使用 600k 迭代，v1 使用 100k 迭代，decrypt 方法根据前缀选择迭代次数。encrypt 始终使用 v2。salt 16 字节、nonce 12 字节、AESGCM 调用正确。无 timing 漏洞——decrypt 失败时返回原值而非抛出异常，但日志记录可能泄露信息。无 padding 漏洞——AES-GCM 是流模式，无 padding。
- *q4_path_validation*: _validate_path 只拒绝 NUL 字节，不拒绝 '..' 或绝对路径，在 local-first 工具中合理。因为用户已有完整磁盘访问权限，'..' 和绝对路径是正常操作（如 export_engram 到任意路径）。NUL 字节拒绝是防止 C 级 API 静默截断。但缺少对路径长度、特殊字符（如 Windows 保留字符）的校验，可视为防御深度不足。
- *q5_doc_clarity*: architecture.md 的'Where to add things'矩阵非常实用，直接对应模块职责。但有一个具体错误：矩阵中'新常量'应放在 storage.py，但 storage.py 中常量定义与 I/O 函数混合，未单独分离。文档说'Constants live in storage.py'，但 storage.py 也包含 _read_json/_write_json 等 I/O 函数，命名不够精确。
- *q6_new_risks*: Mixin 多继承引入 MRO 复杂性：若两个 Mixin 定义了相同方法（如 _ensure_fields），后一个会覆盖前一个，且无警告。re-export 层（core.py 从 storage.py 等 import 并 re-export）增加了模块间循环依赖风险——例如 storage.py 不应 import core.py，但当前未检查。新模块间依赖：retrieval.py import storage.py，context.py import storage.py，但 context.py 未 import retrieval.py 却调用了 self._bigram_similarity（通过 Mixin），依赖隐式且脆弱。
- *q7_readme_confusion*: 量化数据段声称'0 网络调用'，但 comparison.md 提到 read_web_content 是可选网络调用，README 也提到'Only read_web_content (optional)'。'0 网络调用'在核心库中成立，但用户可能误解为完全无网络。对比表将 Cline memories 标记为'❌ Cline-specific'，但 Cline 也支持 MCP，可被其他工具调用，表述不够精确。品牌 FAQ 解释了 piia-engram 包名，但未解释为什么选择这个名称（历史原因？），用户可能仍困惑。

**New findings**:
- [high] **Mixin 方法依赖隐式且脆弱** — context.py 中的 ContextMixin 调用了 self._bigram_similarity（定义在 retrieval.py 的 RetrievalMixin 中），但 context.py 未 import retrieval.py，依赖 Engram 类的 MRO 提供该方法。若 Mixin 顺序改变或某个 Mixin 被移除，将导致 AttributeError。应添加显式依赖检查或文档说明。
- [medium] **re-export 层可能隐藏导入错误** — core.py 从 storage.py、retrieval.py 等 re-export 大量符号（如 from .storage import ...），但未使用 __all__ 控制。用户可能导入未在 core.py 中显式 re-export 的符号（如 _atomic_write_json），导致隐式依赖。应定义 __all__ 或限制 re-export 范围。
- [low] **crypto.py decrypt 失败时返回原值可能泄露信息** — crypto.py 中 decrypt 方法在解密失败时返回原值（包括加密字符串），而非抛出异常或返回空。这可能导致上层代码将加密字符串当作明文处理，潜在信息泄露。应至少记录警告并返回空字符串或标记。

**Suggested next 3**:
1. **补测 context.py 的 LLM 分支和 mcp_server.py 的 SSE 传输** — context.py 覆盖率 70%，LLM 分支（extract_knowledge）无测试，是核心功能回归风险。mcp_server.py 覆盖率 54%，SSE 传输和未测试的 tool wrapper 是安全敏感区域。
2. **显式化 Mixin 方法依赖并添加 MRO 文档** — 当前 Mixin 间隐式方法调用（如 context.py 调用 retrieval.py 的 _bigram_similarity）脆弱且无文档。应添加依赖声明或单元测试验证 MRO 正确性。
3. **改进 crypto.py 解密失败处理** — 解密失败时返回原加密字符串可能导致上层误用。应改为返回空字符串或抛出特定异常，并确保日志不泄露敏感信息。

**Evaluator's own uncertainty**: 最大不确定性是 Mixin 拆分后实际运行时的 MRO 行为——代码审查无法完全验证所有方法调用路径，需要集成测试确认。

---

### Pass 3

**Scores**: {"architecture": 8, "testing": 8, "security": 8, "documentation": 9, "positioning": 8, "overall": 8.2}

**Key Q&A**:
- *q1_architecture_complexity*: Mixin 拆分确实解决了 core.py 过大的痛点，但引入了 MRO 依赖顺序问题。例如 Engram 类定义 class Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)，如果两个 Mixin 定义了同名方法（如 _tokenize），后继承的会覆盖前者。当前未发现冲突，但未来扩展时需小心。另外，reports.py 1103 行仍然很大，只是从 core.py 移出，并未真正瘦身。
- *q2_coverage_honesty*: 78% 覆盖率基线诚实标注了盲区：mcp_server.py 54%（SSE + 未测试的 wrapper）、setup_wizard.py 58%（交互流程）。但 context.py 70% 的解释 'LLM 分支需要 live provider' 可以接受，而 mcp_server.py 的 54% 偏低，尤其是 SSE 传输和 CORS 中间件未覆盖，这些是安全敏感路径。应该补但没补的：SSE 集成测试（test_mcp_e2e.py）已推迟到 v3.15.0。
- *q3_pbkdf2_correctness*: crypto.py 中 PBKDF2 升级正确：v2 使用 600k 迭代，v1 兼容通过 decrypt 分支检测前缀 enc:v1: 并调用 100k 迭代。salt 16 字节、nonce 12 字节、AESGCM 调用正确。没有明显的 timing 或 padding 漏洞，因为 decrypt 失败时返回原始值而非抛出异常，但日志记录可能泄露信息。
- *q4_path_validation*: _validate_path 只拒绝 NUL 字节，不拒绝 .. 或绝对路径，在 local-first 工具中合理，因为用户已有完整磁盘访问权限。但拒绝 NUL 字节是必要的，因为 C 级 API 会静默截断。当前设计是恰当的：不假装是沙箱，只阻止已知的破坏性输入。
- *q5_doc_clarity*: architecture.md 的 'Where to add things' 矩阵非常实用，例如 'A new search/ranking heuristic → retrieval.py'。但 comparison.md 中声称 'Engram is the canonical example of the identity-layer approach' 缺乏外部引用，属于自我定位而非事实。
- *q6_new_risks*: Mixin 多继承引入 MRO 风险：如果两个 Mixin 定义了相同方法，后继承的会覆盖前者。当前未发现冲突，但未来扩展时需小心。重导出层（core.py 中 from .storage import ...）可能造成循环导入，例如如果 storage.py 导入 core.py 会形成循环。新模块间依赖：reconcile.py 依赖 retrieval.py 的 _bigram_similarity，但 retrieval.py 不依赖 reconcile.py，目前无环。
- *q7_readme_confusion*: 量化数据段 '45 MCP 工具' 与证据包中 '43' 不一致（证据包三节说 43，README 说 45）。对比表 'Cline memories/' 列显示 '❌' 表示不支持跨工具，但 Cline 本身是单工具，这个标记可能误导。品牌 FAQ 解释了 piia-engram 包名，但未解释为什么选择这个前缀。

**New findings**:
- [high] **量化数据不一致：README 声称 45 个 MCP 工具，但证据包三节显示 43 个** — 证据包三节量化数据表显示 MCP 工具（全部）为 43，而 README 'By the numbers' 段声称 45。差异未在 changelog 或任何文档中解释。
- [medium] **reports.py 1103 行仍然过大，只是从 core.py 移出** — 虽然 core.py 从 4277 行降至 1083 行，但 reports.py 本身 1103 行，包含 HTML 生成、身份卡导出、健康报告等多个职责。架构文档未解释为什么 reports.py 没有进一步拆分。
- [medium] **mcp_server.py 中 _apply_tool_tier 的测试仅验证 noop 情况，未验证实际过滤** — tests/test_mcp_tools.py 中 test_apply_tool_tier_noop_when_not_core 只测试了 TOOL_TIER='all' 时无操作，未测试 TOOL_TIER='core' 时工具是否被正确移除。

**Suggested next 3**:
1. **统一量化数据并修复 README 与证据包的不一致** — README 声称 45 个 MCP 工具，但证据包显示 43 个。这种不一致会破坏用户信任。应统一为 45 并更新证据包，或统一为 43 并更新 README。
2. **添加 SSE 集成测试（test_mcp_e2e.py）** — mcp_server.py 覆盖率仅 54%，主要盲区是 SSE 传输和 CORS 中间件。这些是安全敏感路径，应添加端到端测试覆盖。
3. **进一步拆分 reports.py（1103 行）** — reports.py 仍然过大，包含 HTML 生成、身份卡导出、健康报告等多个职责。建议拆分为 review.py、identity_card.py、health.py 等模块，保持与架构文档中 'Where to add things' 矩阵的一致性。

**Evaluator's own uncertainty**: 最大不确定性是量化数据不一致（45 vs 43 个 MCP 工具）是否真实存在，因为证据包三节和 README 都来自项目方，但数值矛盾。

---

## Raw

See `results_20260522_122600.json` and `raw_log_20260522_122600.jsonl`.
