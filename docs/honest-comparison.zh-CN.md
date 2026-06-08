# 诚实对比：piia-engram vs mem0 / Basic Memory / ByteRover

> 下方每一条关于竞品的声明都标注日期、脚注到**竞品自己**的公开文档（见
> [Sources](#sources一手来源访问日期-2026-06-08)）。竞品功能会变——引用前请核验其最新文档。
> 更宽泛的赛道分类地图见 [comparison.md](comparison.md)。
>
> 英文版：[honest-comparison.md](honest-comparison.md)

---

## 为什么是这三家

mem0（含其 OpenMemory 组件）、Basic Memory、ByteRover 是用户最常和 piia-engram 放在同一句话里
比较的项目，因为四家都在讲某个版本的"你的 AI 总是忘记你——我们帮你记住"。这句共同的一句话，正是
"记忆"成为红海、也是我们**不**以它为主打的原因。本文只谈我们真正不一样的那一根轴。

> 说明（2026-06-08）：mem0 的独立 **OpenMemory** 项目正在停服（sunset）——其仓库 README 现已引导
> 用户改用 mem0 自托管服务器来获得本地、带面板的记忆。因此本文以 mem0 为主要对比对象，OpenMemory
> 仅作历史背景。

我们**不**声称在对手的主场上赢过它们。mem0 是远比我们强的 agent-memory 引擎；Basic Memory 是一套
优秀的 Zettelkasten "第二大脑"；ByteRover 在可移植编码记忆层上有真实的牵引力。真正诚实的问题不是
"谁记得更多"——而是**"谁让你能治理被记住的东西"**。

---

## 唯一的楔子：你看得见、可回退的治理

piia-engram 的结构性差异是一套**烤进数据层的治理模型**，而不是事后贴在表面的功能：

1. **风险分级 staging → verified。** AI 建议的知识先过风险闸门。低/中风险条目自动转为 verified
   *并留审计记录*；高风险条目（凭证、shell 命令、MCP 配置、权限规则）——以及所有无监督的后台写回
   和 LLM 抽取建议——一律滞留在 `staging` 等你批准，无法自我标记为 `verified`。
2. **身份 + 决策链。** 决策可被更新的决策 supersede，同时保留完整历史（你能看见自己*为什么*改了
   主意，而不只是当前答案）。
3. **字段级静态加密。** 敏感字段（如邮箱、电话）以 AES-256-GCM 在磁盘上加密。
4. **一切可见、可改、可回退、可审计**——并且规划中的 opt-in 严格模式（`ENGRAM_APPROVAL=strict`，
   路线图）可把*所有*写入都路由到审查，供想要"默认最大控制"姿态的用户开启。

> **诚实声明（承重墙）：** 我们的默认**不是**"未经你批准就什么都不存"。低/中风险记忆是自动转
> verified 的。诚实的说法是*能力*——你可以看见、编辑、覆盖、回退任何条目；高风险与无监督写入被设
> 闸门；没有黑箱。"只记你确认过的"这种一刀切说法是过度承诺，我们已从所有公开文案中刻意删除。

在所调查的三家竞品中，**没有一家在其公开文档里同时具备这整套**（截至 2026-06-08）——风险分级审批
**和**身份/决策链**和**本地/开源层的字段级加密。（ByteRover 文档里有 AES-256 静态加密，但仅限其
企业/云层，且没有审批/审计模型；见下方头对头。）这个空白就是整个卖点。

---

## 头对头

### vs mem0 / OpenMemory

| | mem0 / OpenMemory | piia-engram |
|---|---|---|
| 主要任务 | Agent 记忆：存取 agent 做过什么 | 身份层：跨工具存储*你是谁* |
| 存储 | 向量库。库默认本地 Qdrant（`/tmp/qdrant`）；自托管服务器默认 Postgres + pgvector；文档 quickstart 引导新用户走托管云（需注册账号）¹ | 你拥有的本地 JSON 文件 |
| 捕获 | 强自动捕获 | AI 建议；风险分级进 staging/verified |
| 治理 | 截至 2026-06-08，未见对 AI 建议记忆的风险分级 staging→verified 审查文档 ² | 风险分级 staging→verified + 审计日志 |
| 静态加密 | 截至 2026-06-08，未见静态加密文档 ³ | 字段级 AES-256-GCM |
| 何时更适合 | 一个（或多个）agent 需要对大量历史做丰富召回 | 你想让身份/标准跨 Claude Code / Codex / Cursor / Windsurf 跟着你走 |

**mem0 赢在哪：** 规模、语义召回、生态、benchmark 召回数字。如果你需要对一个大型文档/对话语料做强
检索，mem0 是更好的工具——把它和我们*搭配*用，而不是替换。

**我们诚实的优势：** 凌驾于工具之上、用户自有、受治理的身份层，而不是某一个工具内部的 agent
工作记忆。

### vs Basic Memory

| | Basic Memory | piia-engram |
|---|---|---|
| 主要任务 | Markdown + 知识图谱"第二大脑"（Zettelkasten） | 面向 AI 编码工具的跨工具个人身份 |
| 存储 | 本地 Markdown + SQLite 索引的知识图谱（云层：Neon Postgres + Tigris S3）⁴ | 本地 JSON（结构化：profile/lessons/decisions/playbooks） |
| 治理 | 截至 2026-06-08，未见对写入的风险分级审批/staging/审计文档；工具带 read-only/destructive 提示供 agent 参考，"审计日志"仅作为 Teams 套餐的条目出现、无实现细节 ⁵ | 风险分级 staging→verified + 审计日志 |
| 静态加密 | 截至 2026-06-08，未见静态加密文档；本地存储被描述为"磁盘上的纯文本" ⁶ | 字段级 AES-256-GCM |
| 形态 | 笔记式知识库 | 为 AI 冷启动调优的身份库 |
| 何时更适合 | 你想要一个耐用的个人 wiki/笔记图谱 | 你想让 AI 工具从同一个"被批准的你"起步 |

**Basic Memory 赢在哪：** 它是一套真正好用、以人为先的笔记系统，带图谱链接。如果你的目标是个人
wiki，它比我们更合适。

**我们诚实的优势：** 我们为 *AI 冷启动* 这个任务而造（AI 在会话开始读一份经过策展的身份、并在治理
下写回），不是为人工浏览笔记。

### vs ByteRover

| | ByteRover | piia-engram |
|---|---|---|
| 主要任务 | 面向编码 agent 的本地优先可移植上下文层 ⁷ | 面向编码工具的受治理个人身份层 |
| 叙事 | "面向编码 agent 的本地优先 AI 上下文工程" ⁷ | "你能治理、可跨工具移植的记忆" |
| 检索 | 分层文件检索（自称 92.2% 准确率，非向量）⁷ | 确定性 n-gram + 别名（离线、对中文友好） |
| 治理 | 截至 2026-06-08，未见审批/staging/审计模型文档 ⁸ | 风险分级 staging→verified + 决策链 |
| 静态加密 | 企业/云层文档有 AES-256 静态加密 + SOC 2 Type II；标准本地层仅凭证存储（`~/.local/share/brv/`）被记为加密，未见对记忆数据本身的静态加密文档（截至 2026-06-08）⁹ | 本地/开源层即有字段级 AES-256-GCM |
| 何时更适合 | 你想要低摩擦的可移植编码记忆 | 你想要可移植性*加上*控制/可审计 |

**ByteRover 赢在哪：** 牵引力，干净的本地优先可移植上下文故事；更低的安装摩擦——安装是单条
`curl … install.sh | sh` 或 `npm install -g byterover-cli`，然后 3 步 quickstart ¹⁰。

**我们诚实的优势：** 可移植性对双方都是入场券，而且 ByteRover 同样是本地优先、非向量——所以我们
**不**把可移植性或"本地优先"当作对它的楔子。我们的差异是治理层（风险分级审批、审计轨迹、决策历史）
加上*本地/开源层*的字段级加密——ByteRover 仅在其企业/云层文档化了静态加密，标准本地层没有（截至
2026-06-08）。

---

## 我们更弱的地方（直说）

与 [comparison.md](comparison.md) §"竞品更强的地方"一致——在此重复，使本文绝不读成抹黑：

- **安装摩擦：** `pip install` + MCP 配置（两个一次性步骤）vs 竞品的一行命令（ByteRover
  `curl … | sh` 或 `npm install -g`；Basic Memory `uv tool install` / brew）。
- **语义召回：** 我们用确定性字符 n-gram + 别名分词（离线、对中文友好），不用向量嵌入——mem0
  （向量库）和 ByteRover（自称 92.2% 文件检索）公布的召回数字都比我们追求的更高。
- **生态规模：** 我们是个小而专注的项目；mem0 的生态远大于我们。
- **没有 GUI 面板：** 仅 CLI + 生成的 HTML 审查页。
- **Benchmark 叙事：** 我们优化治理指标（审批精确率、冲突率、过期衰减准确率），不是 LongMemEval
  式的召回分数。

---

## 该用哪个（诚实的选型指南）

- **单工具、对大量历史做丰富召回** → mem0。
- **个人 Markdown wiki / 第二大脑** → Basic Memory。
- **低摩擦、本地优先的可移植编码记忆，控制不是优先项** → ByteRover。
- **2 个以上 AI 编码工具，且你想让身份/标准在*你看得见、可回退的治理*下跟着你走** → piia-engram。
  而且你可以把我们和上面任何一家*并用*。

> **免责声明：** 竞品功能会变。上述声明均来自各项目**自己**的公开文档，快照日期 2026-06-08（见
> Sources）。引用前请核验其最新文档。

---

## Sources（一手来源，访问日期 2026-06-08）

上方每条竞品声明都脚注到该项目**自己**的文档/仓库。任何公开使用前请重新核验——竞品文档会变。

1. mem0 存储默认 — docs.mem0.ai/components/vectordbs/config（库默认：本地 Qdrant，`/tmp/qdrant`）；
   docs.mem0.ai/open-source/overview（自托管：Postgres + pgvector）；docs.mem0.ai/platform/quickstart
   （托管云，需注册账号）。
2. mem0 治理 — 在 docs.mem0.ai 与 github.com/mem0ai/mem0 未见风险分级 staging/审批审查文档
   （2026-06-08）。
3. mem0 加密 — 在 docs.mem0.ai 未见静态加密文档（docs.mem0.ai/security 返回 404）（2026-06-08）。
   *OpenMemory 停服：* github.com/mem0ai/mem0/tree/main/openmemory README（2026-06-08）。
4. Basic Memory 存储 — github.com/basicmachines-co/basic-memory README（本地 Markdown + SQLite
   索引知识图谱；云：Neon Postgres + Tigris S3）（2026-06-08）。
5. Basic Memory 治理 — 在 github.com/basicmachines-co/basic-memory 与 basicmemory.com 未见对写入的
   风险分级审批/staging/审计文档；"审计日志"仅作为 Teams 套餐条目出现（2026-06-08）。
6. Basic Memory 加密 — 未见静态加密文档；本地存储被描述为"磁盘上的纯文本"
   （github.com/basicmachines-co/basic-memory、docs.basicmemory.com）（2026-06-08）。
7. ByteRover 定位 + 检索 — byterover.dev、docs.byterover.dev（"面向编码 agent 的本地优先 AI 上下文
   工程"；分层文件检索，92.2% 准确率自称）（2026-06-08）。
8. ByteRover 治理 — 在 docs.byterover.dev 与 byterover.dev 未见审批/staging/审计模型文档
   （2026-06-08）。
9. ByteRover 加密 — byterover.dev（企业/云：AES-256 静态加密 + TLS 1.2+ + SOC 2 Type II）；
   docs.byterover.dev/quickstart（标准层：仅凭证存储 `~/.local/share/brv/` 被记为加密；未见对记忆
   数据本身的静态加密文档）（2026-06-08）。
10. ByteRover 安装 — docs.byterover.dev quickstart（`curl -fsSL https://byterover.dev/install.sh |
    sh` 或 `npm install -g byterover-cli`；3 步设置）（2026-06-08）。
