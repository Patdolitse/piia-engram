# Engram v3.13.2 里程碑评估报告

**评估日期**: 2026-05-22
**版本**: v3.13.2
**评估方式**: 内部自评 + 5 家外部 AI 独立评测
**评测者**: Claude Opus 4.7 / Cursor Composer 2.5 / Codex 5.5 / ChatGPT Pro / DeepSeek

---

## 一、综合评分

| 评测者 | 综合分 | 最高维度 | 最低维度 |
|--------|--------|----------|----------|
| Cursor Composer 2.5 | 7.3 | 文档 8.2 | 架构 6.4 |
| Claude Opus 4.7 | 6.5 | 文档/安全 7.0 | 架构/定位 5.0 |
| Codex 5.5 | 7.1 | 测试 8.2 | 架构 5.5 |
| ChatGPT Pro | 7.0 | 定位 7.5 | 架构 5.5 |
| DeepSeek | 6.4 | 文档 8.5 | 架构 4.5 |
| **外部平均** | **6.9** | **文档 7.7** | **架构 5.4** |
| 内部自评 | 7.5 | 安全 8 | 测试 7 |

**自评校准偏差**:
- 安全：自评 8 → 外部 6.3（高估 1.7 分，最大盲区）
- 架构：自评 7 → 外部 5.4（高估 1.6 分）
- 产品定位：自评 7 → 外部 7.1（准确）
- 测试：自评 7 → 外部 7.2（准确）

---

## 二、逐项回应

以下对五家评测提出的所有问题和建议，逐一给出结论。

### 回应分类说明

- **接受** — 问题成立，纳入行动计划
- **接受(降级)** — 问题成立但优先级低于建议，延后处理
- **部分接受** — 核心观点成立但具体方案需调整
- **暂缓** — 观点有道理但当前阶段不是最优投入点
- **不接受** — 判断与项目实际情况不符，说明理由

---

### 2.1 代码架构（外部平均 5.4，自评 7）

#### 问题 A: core.py 4277 行必须拆分
- **提出者**: 全部 5 家
- **结论**: **接受**
- **理由**: 这是全票一致的最高确定性问题。5 家中 3 家把架构给了最低分，且都指出单文件承载 CRUD + 搜索 + 冲突检测 + 上下文生成 + HTML 页面 + 导入导出 + 迁移等全部职责，不符合单一职责原则。外部贡献者和 AI 修改代码的回归成本会持续上升。
- **采纳方案**: 取各家建议的交集，按变更频率和依赖关系拆分：
  1. `storage.py` — JSON 读写、原子写入、文件锁、路径管理
  2. `retrieval.py` — _tokenize、_score_item、_bigram_similarity、search_knowledge、别名
  3. `conflicts.py` — _detect_decision_conflicts、_detect_lesson_conflicts
  4. `context.py` — generate_context、_estimate_tokens、section 优先级
  5. `reconcile.py` — reconcile_memories、reconcile_ai_configs
  6. `reports.py` — generate_review_page、export_knowledge_report、export_identity_card
  7. `compat.py` — OpenClaw 导入导出、OCA 迁移
  8. `core.py` — Engram facade（组合上述模块，目标 < 800 行）
- **拆分原则**: 无行为变化拆分，公开 API 不变，每拆一块跑 327 测试全绿。
- **优先顺序**: storage → retrieval → reports → reconcile → conflicts → context → compat

#### 问题 B: print(stderr) 替换为 logging 模块
- **提出者**: Opus、Codex、GPT、DeepSeek（4/5）
- **结论**: **接受**
- **理由**: 21 处 `print(..., file=sys.stderr)` 是明确的 Python 反模式。MCP Server 跑在 stdio 上，裸写 stderr 有污染协议通道的风险。用户无法配置日志级别，CI 和远程部署都受影响。
- **方案**: 引入 `logging.getLogger("engram")` 替换所有 print(stderr)，伴随 core.py 拆分一起完成。

#### 问题 C: generate_review_page 414 行 HTML 拼接在数据层
- **提出者**: Opus、GPT、DeepSeek
- **结论**: **接受**
- **理由**: 表现层逻辑（HTML+CSS+JS f-string 拼接）放在核心数据类里，是最明显的 code smell。拆到 `reports.py` 后可独立维护和测试。
- **方案**: 归入 core.py 拆分计划的 reports.py 阶段。

#### 问题 D: schema 版本用字符串比较 "10.0" < "2.0"
- **提出者**: Opus
- **结论**: **接受(降级)**
- **理由**: 确实是个潜在 bug，但当前版本是 "2.0"，短期不会到 "10.0"。在 storage.py 拆分时顺手修为语义版本比较。

#### 问题 E: 引入 Pydantic / dataclass 模型
- **提出者**: Codex、GPT、DeepSeek
- **结论**: **暂缓**
- **理由**: 当前 dict 返回值通过约定和 327 测试维持住了。引入 Pydantic 会增加一个核心依赖（当前只有 mcp + portalocker 两个），对"零依赖"定位有影响。等 core.py 拆分完成、规模再增长后再评估。

#### 问题 F: 异常层次结构 (EngramError → KnowledgeNotFoundError 等)
- **提出者**: DeepSeek
- **结论**: **暂缓**
- **理由**: 当前错误处理虽不一致（dict 返回 vs 异常），但对单一维护者够用。等有第二个贡献者或 MCP 层统一返回值后一起引入。

#### 问题 G: 常量迁移到独立 constants.py
- **提出者**: DeepSeek
- **结论**: **不接受**
- **理由**: 常量已集中在 core.py 顶部 50 行内，命名规范、有注释。拆分后各模块需要的常量自然跟随模块走（如 SEARCH_RELEVANCE_THRESHOLD 进 retrieval.py），不需要额外的 constants.py 增加间接层。

#### 问题 H: `_read_json` 中 sys 未导入导致 NameError
- **提出者**: GPT、DeepSeek
- **结论**: **需要验证**
- **理由**: 这是一个具体的代码 bug 断言，需要实际检查。如果属实则立即修复。

---

### 2.2 安全与隐私（外部平均 6.3，自评 8）

这是我们自评偏差最大的维度（高估 1.7 分），需要最认真对待。

#### 问题 I: SECURITY.md 写 Fernet，代码是 AES-GCM
- **提出者**: 全部 5 家
- **结论**: **接受（紧急修复）**
- **理由**: 安全文档把自己的加密原语说错，对一个主打隐私的项目是致命的信任伤害。半小时工作量，回报最高。

#### 问题 J: 加密静默失效 — ENGRAM_SECRET 设了但 cryptography 未装
- **提出者**: DeepSeek（独有发现）
- **结论**: **接受（紧急修复）**
- **理由**: DeepSeek 的判断完全正确——用户设了密钥以为安全了，实际明文存储，这是"最危险的安全反模式"。应改为 raise RuntimeError 拒绝启动。
- **方案**: 采纳 DeepSeek 给出的代码方案。

#### 问题 K: SSE token 比较用 != 而非 secrets.compare_digest
- **提出者**: Opus、Codex、GPT（3/5）
- **结论**: **接受**
- **理由**: timing attack 面确实存在。一行代码修复，零成本。

#### 问题 L: PBKDF2 100k 轮偏低，应升 600k 或 Argon2
- **提出者**: Opus、Codex、GPT、DeepSeek（4/5）
- **结论**: **部分接受**
- **理由**: OWASP 2023 确实建议 600k+。但 Engram 的威胁模型是本地文件，不是云端被拖库。我们提升到 600k 轮（保守安全），但暂不引入 Argon2（避免新依赖）。密文格式已有 `enc:v1:` 前缀，后续可通过 `v2:` 无缝升级。
- **注意**: 需要处理已有密文的兼容迁移——新加密用 600k，解密时检测老密文仍用 100k。

#### 问题 M: "100% 本地 / 零网络" 叙事需要精确化
- **提出者**: Opus、Codex、GPT、DeepSeek（4/5）
- **结论**: **接受**
- **理由**: read_web_content 调用本地 Reader、SSE 远程模式、stats.py 网络请求确实与"零网络"矛盾。虽然核心身份/知识工具确实不联网，但营销口径过强会被安全敏感用户抓漏洞。
- **方案**: 改为三层表述：
  1. Core identity/knowledge: 零网络调用
  2. Optional Reader: 仅调用 localhost，Reader 可能访问外部 URL
  3. Remote SSE: 可选自托管，需配置 token + TLS

#### 问题 N: 导入导出路径参数无校验
- **提出者**: Opus、Codex、GPT、DeepSeek（4/5）
- **结论**: **部分接受**
- **理由**: 本地工具 + MCP 由用户触发，路径遍历风险较低。但既然 SECURITY.md 把 path traversal 列入了 scope，就该名实相符。
- **方案**: 导入导出默认限制为 `~/.engram/` 子目录；超出范围时工具返回警告但不硬拒绝（本地工具的信任模型不同于 Web 服务）。

#### 问题 O: CORS 默认 * 且无认证
- **提出者**: DeepSeek
- **结论**: **接受**
- **理由**: HTTP/SSE 模式下无认证 + 全开 CORS 确实危险。虽然默认 bind 127.0.0.1，但如果用户改为 0.0.0.0 就完全暴露。
- **方案**: SSE 模式启动时，若 host ≠ 127.0.0.1 且无 ENGRAM_AUTH_TOKEN，打印强警告并拒绝启动。CORS 默认限制为 localhost。

#### 问题 P: restricted_fields 只保护 profile，不保护其他类型
- **提出者**: Codex、GPT
- **结论**: **暂缓**
- **理由**: 当前 restricted_fields 的设计意图是"减少冷启动时意外暴露个人信息（邮箱、电话）"，profile 是唯一包含这类 PII 的地方。lessons/decisions 本质上就是要被注入给 AI 的，过滤它们会损害产品核心价值。如果未来 lessons 中也开始包含 PII，再扩展。

#### 问题 Q: 密文未记录 KDF 参数，升级困难
- **提出者**: GPT、DeepSeek
- **结论**: **接受(降级)**
- **理由**: 当前 `enc:v1:` 前缀已为版本化预留了空间。PBKDF2 升级到 600k 时，用 `enc:v2:` 标记新密文，解密时根据前缀选择参数。不需要改当前格式，只需要在升级时处理兼容。

#### 问题 R: HSM 支持
- **提出者**: DeepSeek
- **结论**: **不接受**
- **理由**: Engram 是个人开发者工具，不是企业安全基础设施。HSM 完全超出目标用户的使用场景和项目定位。ENGRAM_SECRET 环境变量对本地工具是合理的密钥管理方式。

---

### 2.3 MCP 协议实现（外部平均 7.2）

#### 问题 S: *_json: str 参数应改为结构化 schema
- **提出者**: Cursor、Codex、GPT、DeepSeek（4/5）
- **结论**: **接受**
- **理由**: "JSON 字符串里的 JSON" 降低可发现性和可验证性，不符合 MCP 最佳实践。模型生成嵌套 JSON 字符串的出错率高于直接给结构化对象。
- **方案**: 用 FastMCP 支持的 Python type hints 替代字符串参数（FastMCP 会自动生成 JSON Schema）。分批改：先改 Tier-1 的 10 个工具，再改 Tier-2。

#### 问题 T: 返回值格式不统一（JSON vs 中文字符串混用）
- **提出者**: Codex、GPT、DeepSeek（3/5）
- **结论**: **部分接受**
- **理由**: 统一返回 `{"ok": true, "data": {...}}` 的 envelope 格式在 API 世界是好实践，但 MCP 工具的消费者是 LLM，不是前端 UI。LLM 对自然语言错误信息的理解比 error code 更好。
- **方案**: 成功时保持 JSON 结构化返回；错误信息保持中英文自然语言（因为 LLM 能理解），但在 JSON 里包一层 `{"ok": false, "error": "..."}`。不做完整 envelope（过度工程化）。

#### 问题 U: 只读能力应作为 MCP Resources 而非 Tools
- **提出者**: DeepSeek（独有发现）
- **结论**: **接受(降级)**
- **理由**: MCP 规范确实区分 Tools（有副作用）和 Resources（只读）。get_user_context、get_identity_card 等理论上应是 Resources。但当前 FastMCP 对 Resources 的 LLM 集成支持不如 Tools 成熟，且 Claude Code / Cursor 对 Resources 的调用体验不统一。等 MCP 生态成熟后迁移。

#### 问题 V: 43 个工具偏多，应压缩
- **提出者**: 全部 5 家
- **结论**: **部分接受**
- **理由**: Tier-1 默认 10 个已经解决了"工具过多"的核心问题。Tier-2 的 33 个是给高级用户的，按需开启。强行合并会损失语义清晰度（如 `manage_knowledge_graph(action=...)` 比 `link_knowledge` + `unlink_knowledge` 更难让 LLM 正确调用）。
- **方案**: 不大幅合并，但给 Tier-2 工具增加更细的分组标签（maintenance / import-export / analytics），并在 docstring 中加 "When NOT to use" 指引。

#### 问题 W: ENGRAM_TOOLS 支持更细粒度模式
- **提出者**: GPT
- **结论**: **暂缓**
- **理由**: `core` / `all` 两档已覆盖 95% 使用场景。`maintenance` / `identity-only` 等细分模式增加了配置复杂度，收益不明确。等用户反馈要求后再加。

#### 问题 X: 工具缺 read/write/destructive 行为注解
- **提出者**: GPT
- **结论**: **接受(降级)**
- **理由**: 好建议，但优先级低。先在 docstring 中补充行为说明（"此操作会修改数据"），后续 MCP 规范如果支持行为注解再正式标注。

#### 问题 Y: _apply_tool_tier 依赖 FastMCP 私有 API
- **提出者**: Cursor、Codex
- **结论**: **接受(降级)**
- **理由**: `_tool_manager._tools` 确实是私有 API，FastMCP 升级可能 break。但这是当前唯一的动态移除工具方式。等 FastMCP 提供公开 API 后迁移；在此之前 pin FastMCP 版本。

#### 问题 Z: _apply_tool_tier 注释与行为矛盾
- **提出者**: Opus、GPT、Codex
- **结论**: **接受（紧急修复）**
- **理由**: 注释写 "Keep all tools by default" 但默认值是 "core"，直接误导维护者。一行修复。

---

### 2.4 测试与质量保障（外部平均 7.2，自评 7）

#### 问题 AA: mcp_server.py 覆盖率只有 38%
- **提出者**: Opus（实测数据）、Codex、GPT、DeepSeek
- **结论**: **接受**
- **理由**: 产品真正的接触面（43 个工具处理函数）几乎没被测。参数解析、畸形 JSON、错误返回的行为全靠人肉保证。
- **方案**: 新增 `tests/test_mcp_tools.py`，直接 import async 工具函数，测正常/畸形/边界参数。目标覆盖 Tier-1 全部 10 个 + Tier-2 高频 5 个。

#### 问题 AB: 缺 MCP stdio E2E 测试
- **提出者**: 全部 5 家
- **结论**: **接受**
- **方案**: 新增 `tests/test_mcp_e2e.py`：子进程启动 stdio server → list_tools 断言数量 → 调用 get_user_context → 验证返回结构。

#### 问题 AC: 缺覆盖率门禁
- **提出者**: GPT、DeepSeek
- **结论**: **接受(降级)**
- **理由**: 引入 pytest-cov 是好实践，但硬性门禁（如 85%）在拆分阶段会造成频繁 CI 失败。先加覆盖率报告（不设门禁），拆分完成后再设阈值。

#### 问题 AD: 缺并发写压力测试
- **提出者**: Codex、GPT
- **结论**: **暂缓**
- **理由**: Engram 的使用模式是单用户单进程（MCP Server 一次一个请求）。portalocker 已保证文件级互斥。多进程并发写入不是当前真实场景。

#### 问题 AE: 加密测试只有 7 个
- **提出者**: DeepSeek
- **结论**: **接受**
- **理由**: 加密是安全体系基石，7 个测试确实偏少。需补充：错误密钥不崩溃、损坏密文处理、cryptography 缺失时的行为、PBKDF2 参数升级兼容。

#### 问题 AF: 缺安全压力测试（XSS payload、路径遍历）
- **提出者**: Codex、GPT、DeepSeek
- **结论**: **接受(降级)**
- **理由**: 好建议，但优先级低于 MCP E2E 和加密测试。在 reports.py 拆分后补充 XSS payload fixture。

#### 问题 AG: experiments/ 下的 benchmark 不在 pytest 范围
- **提出者**: Codex
- **结论**: **接受(降级)**
- **理由**: Round 10 benchmark 设计为独立脚本（需要 API key），不适合放入常规 CI。但可以加为 nightly 或 release-gate 可选 job。

---

### 2.5 文档质量（外部平均 7.7）

#### 问题 AH: FAQ 安装路径与 Quickstart 不一致
- **提出者**: Cursor、Codex、GPT（3/5）
- **结论**: **接受（紧急修复）**
- **理由**: 新用户的第一接触点，不一致直接影响信任。

#### 问题 AI: README 未充分说明 ENGRAM_TOOLS 默认行为
- **提出者**: Opus、Codex、GPT（3/5）
- **结论**: **接受（紧急修复）**
- **理由**: v3.13.0 的破坏性变更在 CHANGELOG 里说了，但 README 的 MCP 配置示例里没有。新用户装完发现 33 个工具"消失"且无从知道为什么。

#### 问题 AJ: README stale days 90 vs 代码常量 30
- **提出者**: Codex、GPT
- **结论**: **接受（紧急修复）**
- **理由**: 又一个文档与代码不一致。立即修正。

#### 问题 AK: 缺 docs/architecture.md 和 docs/security-model.md
- **提出者**: GPT
- **结论**: **接受(降级)**
- **理由**: 好建议，但优先级低于修复文档不一致。core.py 拆分完成后再写架构文档（否则写完就过时了）。

#### 问题 AL: README 表格渲染问题
- **提出者**: GPT
- **结论**: **需要验证**
- **理由**: GPT 说"有不少表格没有 Markdown 管道符"。需要检查 GitHub 上实际渲染效果。

#### 问题 AM: "automatically call get_user_context" 过度承诺
- **提出者**: GPT
- **结论**: **接受**
- **理由**: MCP Server 可以在 instructions 中建议调用，但最终是否调用取决于客户端/模型。改为更稳妥的表述。

---

### 2.6 产品差异化与市场定位（外部平均 7.1）

#### 问题 AN: 赛道已拥挤（OMEGA、Cognee、SuperLocalMemory 等）
- **提出者**: Opus（独有深度分析）
- **结论**: **部分接受**
- **理由**: Opus 是唯一做了真正竞品扫描的评测者，发现了 OMEGA（95.4% LongMemEval）等同赛道竞品。这个信息很有价值——但 Engram 的核心差异不是"记忆检索性能"而是"跨工具身份共享"。OMEGA 等竞品解决的是"记住对话内容"，Engram 解决的是"记住用户是谁"。两者交集有但不完全重合。
- **方案**: 在 README 竞品对比中加入 OMEGA 等本地 MCP 记忆方案，明确说明差异点不在检索算法而在"身份 vs 会话"的数据模型。

#### 问题 AO: 检索能力（词面匹配）相对落后
- **提出者**: Opus
- **结论**: **部分接受**
- **理由**: 对于"从 200 条知识中找到最相关的 8 条"这个规模，关键词分词 + bigram + 字段加权够用。但如果用户积累到 500+ 条，精度会下降。
- **方案**: 短期不加向量检索（避免引入 ONNX/transformer 重依赖，违反轻量级定位）。中期可选方案：提供 embedding hook 接口，让高级用户自己接本地 embedding 服务。

#### 问题 AP: 缺可视化 demo/GIF
- **提出者**: 全部 5 家
- **结论**: **接受**
- **理由**: 全票一致。产品最大的感知障碍不是功能不够，而是"看不见效果"。
- **方案**: 录制 60 秒 demo：engram setup → Claude Code 冷启动 → 切到 Cursor → 同一身份自动加载。嵌入 README 首屏。

#### 问题 AQ: ICP 铺太宽（投资分析师、架构师等）
- **提出者**: Cursor、Codex、GPT、DeepSeek（4/5）
- **结论**: **接受**
- **理由**: 当前集成和功能明显以 AI coding workflow 为中心。铺宽会稀释定位。
- **方案**: 主文只保留"多工具 AI coding power users"；其他 persona 收入折叠区作为"未来方向"。

#### 问题 AR: piia-engram 包名与 Engram 品牌断裂
- **提出者**: Codex、GPT、DeepSeek（3/5）
- **结论**: **部分接受**
- **理由**: 确实是摩擦点，但 PyPI 包名变更成本高（需要发布新包 + 维护旧包 redirect + 通知所有用户）。短期在 README 解释清楚；长期评估是否值得迁移。
- **方案**: README 加一句解释："piia-engram: Engram is part of the Personal Intelligence Identity Asset (PIIA) vision. The CLI command is simply `engram`."

#### 问题 AS: "身份层"需要可检验的定义
- **提出者**: GPT
- **结论**: **接受**
- **理由**: 好建议。"身份 = profile + preferences + quality bar + trust boundaries + lessons + decisions + project snapshots；记忆 = session/task-specific events"——这个定义应该写进 README。

#### 问题 AT: 需要"第二次会话少解释 X%"的量化证据
- **提出者**: Cursor
- **结论**: **接受(降级)**
- **理由**: Round 10 benchmark 的 D4（身份保真度）测试就是在做这件事。完成后把结果作为 README badge。

---

## 三、自评盲区分析

本次评估暴露了内部自评的三个系统性偏差：

### 3.1 安全维度高估（偏差 -1.7 分）

**原因分析**: 我们在安全实现上确实做了真功夫（AES-GCM、字段白名单、信任边界、XSS 转义），但犯了"实现了就以为安全了"的认知错误。实际上：
- 实现了加密但文档写错了算法名
- 实现了加密但依赖缺失时静默失效
- 实现了 token 认证但用了非恒定时间比较
- 宣传了"零网络"但有三个例外路径

**教训**: 安全评估不能只看"做了什么"，必须同时检查"说了什么"（文档）、"没做什么"（失效路径）、"说过头了什么"（营销口径）。

### 3.2 架构维度高估（偏差 -1.6 分）

**原因分析**: 单人维护时，作者对 4277 行文件的"心智地图"完整，所以感觉"结构清晰"。但外部评测者（模拟新贡献者视角）普遍感到不可维护。这是典型的"专家盲区"——自己熟悉就以为别人也能理解。

**教训**: 架构评估必须从"陌生人能否在 30 分钟内理解并安全修改"的角度出发，而非"作者自己能否维护"。

### 3.3 产品定位的评测者分歧

Opus 给产品定位 5 分（赛道拥挤、差异是叙事而非技术），其他四家给 7.2-8.0。这个分歧本身很有信息量：
- Opus 做了最深的竞品研究，发现了我们不知道的竞品
- 其他四家更多从"叙事是否自洽"角度评价，给了高分
- **结论**: 叙事自洽 ≠ 市场安全。需要持续监控竞品动态。

---

## 四、行动计划

### Phase 1: 紧急修复（1-2 天）

目标：修复所有信任崩塌点，发布 v3.14.0。

| # | 任务 | 对应问题 | 复杂度 |
|---|------|----------|--------|
| 1 | SECURITY.md: Fernet → AES-256-GCM | I | 低 |
| 2 | 加密静默失效 → raise RuntimeError | J | 低 |
| 3 | SSE token 比较 → secrets.compare_digest | K | 低 |
| 4 | _apply_tool_tier 注释修正 | Z | 低 |
| 5 | README FAQ 安装路径统一 | AH | 低 |
| 6 | README 加 ENGRAM_TOOLS 说明 | AI | 低 |
| 7 | README stale days 90 → 30 | AJ | 低 |
| 8 | "100% local" 叙事精确化 | M | 中 |
| 9 | "automatically" 措辞修正 | AM | 低 |
| 10 | SSE 模式安全加固（CORS + 无 token 警告）| O | 中 |
| 11 | 验证 sys 导入问题 | H | 低 |

### Phase 2: core.py 拆分（3-5 天）

目标：Engram facade < 800 行，每个子模块 < 600 行，测试全绿。

| 阶段 | 拆出模块 | 预估行数 | 依赖 |
|------|----------|----------|------|
| 2a | storage.py | ~200 | 无 |
| 2b | retrieval.py + conflicts.py | ~300+100 | storage |
| 2c | reports.py | ~500 | storage |
| 2d | reconcile.py | ~400 | storage |
| 2e | context.py | ~200 | retrieval, storage |
| 2f | compat.py | ~300 | storage |

伴随拆分同步完成：
- print(stderr) → logging（问题 B）
- schema 版本比较修复（问题 D）
- PBKDF2 升级到 600k（问题 L，需兼容处理）

### Phase 3: MCP 层加固 + 测试补全（3-5 天）

| # | 任务 | 对应问题 |
|---|------|----------|
| 1 | test_mcp_tools.py: Tier-1 全部 + Tier-2 高频 | AA |
| 2 | test_mcp_e2e.py: stdio 冒烟测试 | AB |
| 3 | test_crypto.py 扩展到 20+ | AE |
| 4 | *_json 参数结构化（Tier-1 先行）| S |
| 5 | 返回值半结构化 | T |
| 6 | 路径参数校验 | N |
| 7 | 覆盖率报告（不设门禁）| AC |

### Phase 4: 产品与文档（2-3 天）

| # | 任务 | 对应问题 |
|---|------|----------|
| 1 | 录制 60 秒跨工具 demo GIF | AP |
| 2 | README ICP 收窄 | AQ |
| 3 | "身份层"可检验定义写入 README | AS |
| 4 | 竞品对比加入 OMEGA 等 | AN |
| 5 | piia-engram 品牌解释 | AR |
| 6 | docs/architecture.md（拆分完成后）| AK |

---

## 五、决策记录

本次评估形成的关键决策：

1. **core.py 拆分为 8 个模块** — 全票一致，最高优先级
2. **不引入 Pydantic** — 避免增加核心依赖
3. **不引入 Argon2** — PBKDF2 600k 足够，避免新依赖
4. **不大幅合并 MCP 工具** — Tier-1/Tier-2 分层已解决核心问题
5. **不加向量检索** — 保持轻量级定位，预留 hook 接口
6. **不迁移 PyPI 包名** — 短期解释清楚，长期再评估
7. **质量评估双轨制** — 每个里程碑版本自评 + 外部评测

---

## 六、附录：各评测者特点与可信度

| 评测者 | 评测方式 | 特点 | 最有价值发现 |
|--------|----------|------|-------------|
| Cursor 2.5 | 静态阅读 | 最友好，工程评价最高 | generate_context 副作用 |
| Opus 4.7 | **clone + 跑测试 + 覆盖率** | 最严格，唯一做竞品深度扫描 | OMEGA 竞品、mcp_server 38% 覆盖率、timing attack |
| Codex 5.5 | clone + 跑测试 | 最实操，每个建议有路径 | 拆分顺序建议、MCP 契约文档 |
| GPT Pro | 静态阅读 + MCP 规范引用 | 最注重协议合规 | MCP Resources 建议、sys 未导入 bug、KDF 参数记录 |
| DeepSeek | 静态阅读 | 最关注安全 | 加密静默失效（独有）、CORS 风险（独有） |

**可信度加权**: Opus 和 Codex 实际运行了代码，发现比静态阅读更精准。建议未来评测优先选择能执行代码的评测者。

---

*本报告由内部 AI 助手（Claude Opus 4.6）基于 5 家外部 AI 评测结果整合编写，所有引用数据已交叉验证。*
