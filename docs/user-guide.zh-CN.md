# Engram 用户指南

> English: [Engram User Guide](user-guide.md)
>
> 本指南适用于当前版本，以"行为"为主线：Engram 到底做什么、你实际要做什么、
> 以及没有你点头绝不会发生什么。只想 5 分钟跑通，先看
> [快速上手](quickstart-first-value.zh-CN.md)；想了解数据边界，看
> [信任模型](trust.md)。

Engram 是一个**本地优先的 AI 工具个人记忆与身份层**。它让 Claude Code、Codex、
Cursor、Windsurf、Claude Desktop 等兼容 MCP 的工具共享同一份你已认可的上下文
——偏好、标准、经验、决策、操作手册、项目快照——这样你不必每次对话、每次换工具
都重新解释自己。

---

## 0. 心智模型：Engram 是什么，不是什么

请先读这一节，它能消除大部分"到底有什么在跑"的困惑。

**Engram 不是后台守护进程。** 没有任何东西 24/7 运行，也没有一个 agent 在
偷偷盯着你的电脑。Engram 是三样东西协同工作：

1. **一个本地文件库**，位于 `~/.engram/`（纯 JSON 和 Markdown）。这是唯一的
   事实来源，归你所有。
2. **一组 MCP 工具**，你的 AI 客户端用它们来读写这个库。
3. **指令规则**，写在各工具的全局配置里（如 `~/.claude/CLAUDE.md`、`AGENTS.md`），
   告诉 AI *什么时候*该调这些工具。

所以当某件事看起来"自动"时，真实发生的是：你的 AI 工具——按它的指令规则——
决定调了一个 Engram MCP 工具，读或写了一个本地文件。**没有 AI 工具打开，就什么
都不会发生。** 这是刻意设计的：让整个系统透明、可检视、完全受你掌控。

| 常见误解 | 实际情况 |
|---|---|
| "Engram 会同步到云账号。" | 没有云账号、不强制登录、默认不做云同步。数据在本地。 |
| "它会自动记录我做的一切。" | 只在 AI 工具调用写入工具时才记录，通常是因为你要求或某条规则触发。 |
| "有个服务在后台索引我的文件。" | 索引和去重是在某次工具调用*内部*按需跑的，不是后台进程。 |
| "AI 能悄悄把任何东西升级成可信记忆。" | 高风险写入会被门控；无人监督的写回一律强制送审（见 §4）。 |

---

## 1. 安装与连接

```bash
pip install piia-engram
engram setup
```

`engram setup` 会探测你的 AI 客户端，**明确列出它将改动的配置文件，并在写入
MCP 连接前请你一键确认**。每次外部写入都先备份，选"否"则所有配置原封不动。
非交互/CI 场景用 `engram setup --apply-external-config` 跳过确认。

默认你会得到 **18 个核心 MCP 工具**（`ENGRAM_TOOLS=core`）——足够覆盖安装、
首个价值、日常召回、会话收尾。进阶工具集（审查队列、导入导出、治理、迁移、
Playbook 管理）默认关闭，需要时用 `ENGRAM_TOOLS=all` 开启。

Engram 不会自己读取其它 AI 工具的文件。想把它们已有的记忆（记忆文件、`CLAUDE.md`、
`AGENTS.md`、`.cursorrules` 等）带进来，运行 `engram import-memories`：先列出条目，
确认后写入待审区（用 `engram review` 审核）。

- 按工具的安装说明：[Claude Code](integrations/claude-code.md) ·
  [Codex](integrations/codex.md) · [Cursor](integrations/cursor.md) ·
  [Hermes](integrations/hermes.md)
- 随时用 `engram doctor` 检查健康状态。

---

## 2. 一次对话拿到第一个价值

Engram 的价值出现在你*第二次*跟 AI 说话时——它已经知道你之前告诉过的事。
想立刻体验一次：

1. 在已连接的工具里，给它一条稳定偏好，例如
   *"记住我喜欢简洁的回答，并附上明确的验证命令。"*
   AI 会调一个写入工具（`memory_store`、`add_lesson`、`add_decision`、
   `add_playbook` 或 `update_identity`）。
2. 开一个**全新**对话——同一个工具，或同一台机器上另一个已连接的工具。
3. 问一个会用到那条偏好的问题。新对话会直接从你说过的内容起步，而不是让你
   重新解释。

如果召回没触发，明示一次（*"用 Engram 搜一下我存的关于简洁回答的偏好"*），
并参考
[快速上手的排查章节](quickstart-first-value.zh-CN.md)。

---

## 3. 跨工具与跨会话续接

因为每个工具读写的是同一份 `~/.engram/` 库，Claude Code 写的经验 Codex 立刻能
看到，Cursor 记的决策在 Claude Code 下一个会话也在。全程不涉及云同步。

换工具或接续昨天的工作时，推荐的交接回路：

1. 上一个工具调 `wrap_up_session()`（或 `save_agent_context()`）保存会话。
2. 下一个工具开场就调 `get_resume_brief()`——一段 30 秒交接，点明当前项目、
   上次活动、下一步动作，以及一条信任提示。
3. AI 先读这段交接，再决定是否需要让你重复上下文。

`wrap_up_session` 默认只做轻量收尾，不执行完整 reconcile。`run_reconcile=True`
仍被接受，但不再导入任何内容；其它 AI 工具的记忆只能通过 `engram import-memories` 导入。

三档恢复，由快到慢：

| 档位 | 方式 | 速度 |
|---|---|---|
| Quick | 直接读 `~/.engram/quick_context.md` | 毫秒级 |
| Resume | `get_resume_brief()` | <1 秒 |
| Standard | `get_user_context(level="standard")` | <1 秒 |
| Full | `get_user_context(level="full")`（含冲突+同步） | 1–2 秒 |

每条记录都带 `source_tool` 字段，你随时能追溯是哪个工具写的。多工具共存、
身份字段溯源、冲突处理、以及只含元数据的续接证明等完整内容，见
[跨工具指南](cross-tool-guide.md)。

---

## 4. 治理与审批：AI 提议，重要的由你审

Engram 把长期记忆当作**归你所有的资产**，而不是某个 agent 可以悄悄改写的东西。
新的 AI 提议知识在生效前，会先过一道**风险闸门**分级：

- **低/中风险**（大多数偏好、经验、项目规则）**自动 verified**，下个会话即可用，
  让日常路径保持低摩擦。
- **高风险**（凭证值、可执行命令、权限或 MCP 配置改动）送 **staging**，等你
  审查后才生效。
- **无人监督的后台写回**无论风险高低一律强制送 staging，且 LLM 抽取的建议
  **不能自己把自己标成 verified**。

想要更严的姿态，设 `ENGRAM_APPROVAL=strict`——这时**每一条**写入（包括试图在
内容里自己钉死 `tier` 的调用方）都会先送 staging 等你批准。

staged 条目始终在你掌控之中：

- `review_staging(action="list")`——查看待审内容（冷启动 `get_resume_brief` 也会带出
  待审数量，含高风险项）。
- 在审查界面里批准、编辑、归档或拒绝。
- 在终端里运行 `engram review interactive`（或 `engram review -i`），逐条显示待审
  提案（类型、内容、风险、来源、可能的重复及差异、取代关系），输入一个字母加回车：
  `a` 批准、`r` 拒绝（可写理由，只记在回执里，经 MCP 的 `get_audit_log` 读不到）、`s` 取代一条已批准条目（输入其 id）、`k` 跳过、
  `v` 查看全文、`q` 结束。确认汇总时输入 `y` 才写入；`n`、输入结束或 Ctrl+C 都不写入。
  它与 `engram review apply` 走同一条应用路径，回执相同。没有终端时改用
  `engram review export --out <目录>` 和 `engram review apply <marks.json>`。
- 导出会生成 `review.md`（每条提案一张卡片，含版本号）、`ids.json`（id 列表）和
  `marks-template.json`（每条提案一项，填好后另存为 `marks.json`）：
  `{"id": "...", "mark": "approve", "expected_version": 2}`。`mark` 可取 `approve`、
  `reject`、`edit-type:<类型>`、`supersede:<id>`（批准它，作为已批准条目 `<id>` 的替代，
  须同种类、同作用域）、`retire`、`restore` 或 `skip`（保持待审）。可选字段：拒绝时的
  `reason`（你的备注，只记在本次回执里，不写入拒绝记录）和 `expected_version`（条目在此之后
  被改动则跳过）。一次运行里同一条目只能被取代一次（否则文件在写入
  任何内容之前就会被拒绝）。普通的批准与拒绝先执行，带取代关系的 mark 后执行，因此可以在
  一次运行里同时批准某条目和取代它的提案；你拒绝的取代目标只让指向它的那一条失败。演练
  按同样的顺序模拟。文件里所有 mark 都失败时，`engram review apply` 以非零码退出。
- Playbook 在被信任使用前始终需要显式审查；Engram 绝不悄悄执行流程——它把步骤
  作为被动参考交给你的 AI 工具，并追踪上报的执行结果。

AI 拿到什么，各个入口规则一致：

- AI 不用开口就拿到的上下文（冷启动、接续简报、会话开始钩子、`get_recall`、
  `get_relevant_knowledge`）只含已审核且当前有效的条目；待审、被新版本取代、
  已归档的条目都不出现。召回只认明确标为已审核的条目：未知的 tier、缺少 status、
  被拒绝或已弃用的标记，都会让条目被排除。
- `search_knowledge` 把待审条目单独列在 `pending` 分组里（每条标
  `pending_untrusted`），不和结果混排；被取代的条目默认不返回，传
  `include_superseded=true` 时单独分组返回。`{"tier": "archived"}` 过滤返回空；
  `engram dock-search` 每类合计最多显示 `--limit` 条。
- 按 id 读取（`get_knowledge_history`、`explore_knowledge`）仍会返回被取代的条目，
  并注明取代它的条目（`superseded_by`）。
- 内容超出 token 预算时，返回里会说明省略了什么（`omitted`：条数、id、段名），
  文本形态的上下文末尾加一行，例如 `已省略 3 项（预算）：lessons, decisions`（冷启动）或
  `Omitted 3 items (budget): lessons, decisions`（接续简报与钩子）。
  `engram preview` 会显示被裁掉条目的摘要。

每条记录都带生命周期元数据（`memory_state`、`approval_status`、
`risk_level`/`risk_flags`、`provenance`、`approval_required`），状态始终可见。
完整细节以及可选的按调用方治理层（`ENGRAM_GOVERNANCE=1`，默认关）见
[信任模型](trust.md) 和 [治理](governance.md)。

---

## 5. 隐私与数据主权

这是 Engram 坚持本地优先的核心原因。

**什么留在本地。** 默认所有东西都在 `~/.engram/`（或你用 `ENGRAM_DIR` 指定的
目录）里，以纯 JSON/Markdown 形式：身份、知识、Playbook、项目快照、近期上下文、
每日日志。

**默认行为：**

- 没有托管账号、不强制订阅、默认不做云同步。
- Engram 每天发送一次匿名使用信号（随机安装 ID、版本、系统、Python 版本、AI 客户端名称、日期）。关闭方式：`engram telemetry off`、`ENGRAM_TELEMETRY=0` 或 `DO_NOT_TRACK=1`；CI 和容器环境中自动不发。
- 详细使用统计默认关闭，开启后先写本地日志；远程发送（`engram telemetry remote on`）和每周反馈报告（`engram telemetry feedback on`）都是**单独的显式 opt-in**。知识内容、提示词、AI 回复、文件路径、邮箱、IP 地址从不被采集。
- 审计日志**默认开启**；它把读写操作记录到本地 `~/.engram/audit.log`（纯 JSON-lines，绝不外传）。可用 `ENGRAM_AUDIT=0` 关闭。
- 按调用方治理层**默认关闭**；用 `ENGRAM_GOVERNANCE=1` 开启。当同一份记忆同时接给多个 AI 工具、自动化流程或远程桥接时建议开启；`engram status` 和 `engram doctor` 会显示它当前是否启用。
- `engram setup` 不会在未经你确认（或显式 `--apply-external-config` 标志）的
  情况下改动外部客户端配置。

**你的控制手段：**

- 直接查看/编辑 `~/.engram/` 下的本地 JSON/Markdown。
- 用 `get_identity_card` 导出便携身份卡。
- 在提升知识前先审查；归档或更新过期条目。
- `engram telemetry off` / `engram telemetry preview` 控制并检视遥测载荷。
- 用 `pip install "piia-engram[secure]"` + `ENGRAM_SECRET` 为支持的敏感字段
  开启可选的字段级加密。

**迁移或备份数据：** 复制整个 `~/.engram/` 文件夹即可。那就是你全部的记忆——
没有云端副本需要对账。

**什么不该存。** Engram 是个人 AI 上下文，不是密钥管理器。**不要**存密码、
API key、OAuth token、私钥、客户 PII 或受监管数据。如果某条经验需要敏感上下文，
存不敏感的推理部分，把密钥本身放进真正的密钥管理器。

**诚实的边界。** Engram 是一个透明的、本地优先的策略层——不是沙箱。任何能访问
`~/.engram/` 文件系统的本地进程都能读你的文件；MCP 调用方身份是自报的；可选
加密是字段级的，不是全盘加密。更强隔离请用操作系统权限和磁盘加密。完整的数据
流向细节见 [信任模型](trust.md) 和 [PRIVACY.md](../PRIVACY.md)。

---

## 6. 日常使用与维护

- **让 AI 记住：** *"记住这个……"* 或 *"把这条存成经验。"*
- **让 AI 回忆：** *"我之前关于……怎么说的？"* 或 *"按我一贯的风格来。"*
- **定期审查 staging 队列**（比如每周一次）用 `review_staging(action="list")`——尤其
  当你开了 `ENGRAM_APPROVAL=strict`；在终端里用 `engram review interactive` 逐条审核。
- **检查健康**用 `engram doctor`（身份完整度、知识量、过期项、近重复、决策冲突、
  编码健康、健康分）。它是本地诊断——分享前先审。
- **保持整洁：** 知识按类型衰减（偏好约 90 天、调试技巧约 15 天），每类都有
  上限，库不会无限膨胀。`doctor` 标出过期项时及时归档或更新。
- **可选——升级检索：** 默认关键词检索开箱即用。如果需要跨语言召回（用英文
  查询找到中文笔记），开启混合检索：`pip install "piia-engram[vector]"` 加
  `ENGRAM_SEARCH=hybrid`，或在 `engram setup` 向导里一键开启。
  详见 [hybrid-search.zh-CN.md](hybrid-search.zh-CN.md)。

---

## 7. 常见问题

**Engram 会上传我的数据吗？**
不会。你的记忆都在 `~/.engram/`。Engram 每天发送一次匿名使用信号（随机安装 ID、版本、系统、Python 版本、AI 客户端名称、日期），
可用 `engram telemetry off`、`ENGRAM_TELEMETRY=0` 或 `DO_NOT_TRACK=1` 关闭。详细使用统计默认关闭，即便开启也只在单独 opt-in 后发送
匿名计数——绝不发你的内容。

**我换了 AI 工具，记忆还在吗？**
在。所有连到 Engram MCP 的工具读的是同一份本地库。

**我说了"记住"，下个会话却不知道，为什么？**
AI 可能只把它存进了自己的私有记忆，没存进 Engram。用 `search_knowledge` 验证；
没有就明示：*"用 add_lesson 把那条存进 Engram。"*

**AI 会不会把我的记忆塞满垃圾？**
去重会链接或拒绝近似重复的写入，高风险内容被门控到 staging，
`ENGRAM_APPROVAL=strict` 还会把*所有*写入都送你审查。

**怎么迁到新电脑？**
复制 `~/.engram/` 文件夹。（多机实时同步目前还没内置。）

**两个工具同时写会损坏数据吗？**
不会。文件级锁会把并发写入串行化。

**怎么知道 Engram 在某个工具里正常工作？**
跑 `engram doctor`，或让工具调 `get_user_context` / `get_resume_brief`。

更多跨工具问题见
[跨工具指南 FAQ](cross-tool-guide.md#6-faq)。

---

## 8. 下一步去哪

- [快速上手：约 5 分钟拿到第一个价值](quickstart-first-value.zh-CN.md)
- [信任模型](trust.md)——数据边界和什么不该存
- [跨工具与跨会话指南](cross-tool-guide.md)
- [治理](governance.md)——可选的按调用方策略层
- [遥测与隐私](telemetry-privacy.md) · [PRIVACY.md](../PRIVACY.md)
- [诚实对比](honest-comparison.zh-CN.md)——Engram 在记忆数据库、仓库规则文件、
  原生工具记忆之间的定位
- [架构](architecture.md)——内部如何运作

---

## M9：会话结束保存边界

`wrap_up_session` 是轻量的会话结束保存。默认不会运行完整 reconcile，也不会默认执行外部 AI 记忆或配置的 full reconciliation。

`run_reconcile=True` 仍被接受，但不再导入任何内容。需要导入其它 AI 工具的记忆时，由用户在终端运行 `engram import-memories`（先预览，确认后进入待审区）；普通会话收尾继续保持 lightweight session-end save。
