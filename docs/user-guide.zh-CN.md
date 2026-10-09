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

默认你会得到 **19 个核心 MCP 工具**（`ENGRAM_TOOLS=core`）——足够覆盖安装、
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
   `update_identity` 的身份修改在所有模式下均等待本地批准：运行
   `engram review interactive`，比较旧值/新值后批准。自动上下文不包含待审提案。
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

- **身份提案在所有模式下都需审核。** MCP `update_identity` 不直接修改身份资料、偏好、
  工作风格、质量标准或信任边界。用 `engram review` 列出，`engram review show <id>`
  查看旧值/新值，或 `engram review interactive` 批准/拒绝。文件审核用
  `engram review export --out <dir>`，再运行
  `engram review apply <marks.json> --operator <name> --yes`；身份只支持 approve/reject/skip。
  拒绝不改已批准身份，拒绝记录不保存正文。相关原值被本地修改后，批准会返回
  `identity_conflict`；请拒绝旧提案，再基于当前值重新提案。拒绝指纹包含原值：
  对不同原值请求相同新值可以重新提案，对相同原值的相同修改仍会被拒绝，备份恢复后也一样。
  旧拒绝记录没有原值指纹，仍需用 `engram review untombstone <id>` 显式撤销拒绝。
  批准被中断时提案保持 `applying`；重试批准或运行 `engram doctor --fix` 完成它。
  恢复前不能拒绝该提案；后续本地修改会保留，并报告 `identity_conflict`。本地 setup 和
  `engram dock-set-lang` 等 Owner 命令仍直接生效。

- `review_staging(action="list")`——查看待审内容（冷启动 `get_resume_brief` 也会带出
  待审数量，含高风险项）。
- 决定待审条目（批准、拒绝、归档、恢复）由你在本地 `engram review` 中完成，任何审批模式都一样。
  AI 经 MCP 只能列出和预览（`review_staging` 且 `dry_run=true`），不能决定：落盘的批量审核、
  `apply_text`、改待审条目的 tier 或 status、归档或确认它、批准/拒绝/删除/恢复待审 playbook，
  以及 `onboard_accept`，都返回 `local_review_only`，不写入任何内容（onboard 候选请在本地用
  `engram onboard-accept` 接受）。
- 读取知识只累加访问次数，不刷新 `last_reviewed`；它只由你的确认和复习操作更新，
  因此 `get_stale_knowledge` 仍会列出你还没复习的条目。
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
  `reason`（你的备注，只记在本次回执里，不写入拒绝记录）和 `expected_version`（只对
  approve、reject、supersede 生效；条目在此之后被改动则跳过）。同一个 id 只能有 approve /
  reject / supersede / skip 中的一个，一次运行里同一条目只能被取代一次（否则文件在写入任何
  内容之前就会被拒绝）。执行顺序：普通的批准与拒绝 → 带取代关系的 mark → edit-type →
  retire / restore。因此可以在一次运行里同时批准某条目和取代它的提案；你拒绝的取代目标只让
  指向它的那一条失败。取代链请按从旧到新的顺序排列；如果某条的取代目标在同一次运行里被批准，
  它也会自动排在目标之后执行（交互审核依赖这一点）。取代的"同类型"检查按本次 edit-type 执行后两条的
  `type:` 标签判断：同一文件里把提案的类型改成与目标相同可以成功，改成别的类型会被拒绝。已归档的
  playbook，或本次被取代而归档的 playbook，其 edit-type 会被跳过（记为跳过，不算失败），所以检查按它
  现有的标签判断。检查按计划中的类型进行：如果决策或经验的 edit-type 随后失败，同一次运行里已经完成的
  取代不会回滚。每条 edit-type 的回执项记录原标签（`from`，原本没有则为 null）和新标签（`to`）。演练按同样的顺序模拟，显示的就是实际
  执行的结果；回执的每一项注明所在阶段，并按执行顺序列出 id。文件里所有 mark 都失败时，
  `engram review apply` 以非零码退出。
- AI 经 MCP 写入的 playbook（`add_playbook`、`kind="playbook"` 的 `memory_store`、从会话
  起草的手册）在任何审批模式下都是提案：进入待审区，等你用 `engram review` 批准，批准前
  不进入自动召回。AI 对已批准手册的改写（经 `manage_playbook` update 或 `update_knowledge`
  改步骤、标题、触发词等）也是提案：在你批准新版本之前，已批准的版本照常可用、不被改动。待审手册也不能被执行：`playbook_execution` 返回 `not_approved`，`get_playbooks`
  列出时标上 `pending_untrusted`。你在本地添加的手册（例如 `engram playbook install`）不受影响。
- AI 新增的决策如果与一条已审核决策问题相同、选择不同，在任何审批模式下都是取代它的提案
  （`pending_supersedes`）：你批准新决策之前，已审核的那条照常使用。你在本地添加的决策仍会立即
  建立取代关系。
- Playbook 的 id 由 Engram 生成：AI 随新 playbook 发来的 id 会被忽略，新增也不会占用已有的 id。
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

### 钉住必须保留的条目

`engram pin <id>` 钉住一条已审核的 lesson、decision 或 playbook（id 有歧义时用
`--kind` 指定类型；`engram pin --list` 列出已钉住的条目；`engram unpin <id>` 解钉）。
钉住的意思是"保留并优先展示"，不是"永远正确"：

- 只有本地命令能钉住或解钉，且只能钉住已审核、当前有效的条目（待审、已归档、
  已被取代的会被拒绝并说明原因）。钉住会记入审计日志，条目版本号不变。
- 钉住的条目不受生命周期归档、容量规则和导入影响（本地导入与经 MCP 导入都一样）：合并
  导入跳过它，替换导入把它留在原位，备份里指向它的 supersedes 关系会被丢弃，这些都在预览和
  结果里列出；会批准取代它的提案的导入被拒绝（`pinned_target`）。备份导入永远不会带入钉住状态。
- 经 MCP 不能修改、归档、合并或删除钉住的条目：工具返回 `pinned_entry`，不写入任何内容。
  AI 仍可用 `add_lesson` / `add_decision` / `add_playbook` 加 `supersedes=<id>`
  （以及 `supersedes_expected_version`）提交修订提案；无论哪种审批模式，这类提案都
  等待你的本地审核（`engram review apply` / `engram review interactive`）。AI 经 MCP
  无论走哪条路径都不能批准它：批量批准、审查页的 promote 列表、改 tier 返回
  `local_review_only`，导入返回 `pinned_target`，`onboard_accept` 也返回 `local_review_only`；都不写入。
  审核卡会提示目标是钉住条目。提案只能取代同一作用域内的有效条目：其它项目的条目、项目提案
  取代全局条目（或反过来）、已归档的条目都会被拒绝，返回 `supersedes_target_not_applicable`
  与 `reason`（`different_project`、`scope_mismatch`、`archived`）。你批准后旧条目被取代并自动解钉（记入审计）。
  你自己归档它也会解钉。
- 合并另外两条条目（`merge_knowledge`）时，钉住条目完全不变，包括 `related_ids`。
  它与被合并掉那条的链接保留，被合并掉的条目仍能按 id 读取。经 MCP 合并时，所有第三条目
  都不变，不论是否钉住；只修改你提供了版本号的两个操作数。本地本人合并仍会将未钉住条目的
  链接改为指向保留下来的条目。
- AI 拿到的上下文里，钉住的条目在各自分组（lessons、decisions、playbooks）内排在最前，
  条数上限或预算裁剪时先舍弃未钉住的条目。`search_knowledge` 里钉住只在相关度相同时
  决定先后，不会出现在无关的搜索结果里。`engram preview` 会标出钉住的条目。

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
没有云端副本需要对账。JSON 备份（`export_engram`）用本地命令 `engram import <backup.json>`
导回（默认只预览；`--apply --yes` 才写入，`--overwrite` 为替换）。经 MCP，`import_engram`
只能预览导入（`dry_run=true`）；要求真正导入时返回 `local_only`，不写入任何内容。OpenClaw
文件用 `engram import --format openclaw --memory MEMORY.md [--soul SOUL.md] [--user USER.md]`
导入（默认只预览；`--apply --yes` 才写入）：经验进入待审区并留下回执，USER.md / SOUL.md
生成身份资料、偏好和质量标准的待审提案。先在本地比较旧值/新值，再批准；
`--apply --yes` 只导入提案，不代表批准身份修改。

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

## Hook 可靠性与离线处理

Claude Code 的 Stop/PreCompact/PostCompact 和 Cursor 的 stop/sessionEnd 写入型 hook 现在只发布一个本地小事件，并以状态码 0 退出，不初始化记忆存储，也不等待其锁。Cursor 知识回写仍须显式启用。检查点和压缩日记延后保存；提取出的知识始终进入 staging，由所有者审核，严格模式同样如此。

写入型 hook 的 stdin 上限为 128 KiB，模块加载后的读取期限为一秒。管道一直打开、输入超限或读取失败时，尽力记录本地诊断并以状态码 0 退出，不发布不完整事件。

在方便时处理队列：

```sh
engram hooks drain --dry-run --json
engram hooks drain
engram hooks drain --json
engram doctor --json
```

`--dry-run` 只报告数量，不写入任何内容。正常处理按最旧事件优先；相同事件 ID 重放不会重复生成提案或归档记录。可重试失败或处理器忙时命令返回 1，用法错误返回 2，其余返回 0。成功处理也可能报告已隔离的坏事件，请检查该数量。Doctor 只读报告待处理数量/字节数、最旧事件年龄（秒）、隔离数量和不完整文件，不处理队列。已有本地 staging 提取路径（包括已启用的 watcher 回写）也会尝试处理队列。MCP 启动和 MCP 工具调用均不处理；如果只使用这些操作，请自行运行本地 drain 命令。

事件位于 `<store>/hooks/spool/`，待处理上限为 1,000 条或 16 MiB，单事件上限为 128 KiB。超限时最旧文件进入 `quarantine/` 并附原因文件；格式错误事件也进入该目录。并发发布期间，维护锁忙可能造成短暂超限，下一次发布或 drain 会恢复上限。隔离文件和完成凭据保留，不计入待处理上限。请显式检查隔离内容，保留凭据以维持事件 ID 去重。中断留下的 `.partial` 文件单独计数，保留供检查。存储锁、磁盘或处理异常会保留待处理事件供下次重试；若发布本身失败，hook 尽力在 `<store>/logs/hooks.log` 记录诊断，存储不可访问时回退到存储同级的 `<store-name>.hooks.log`。两处均不可写时，无法保证捕获成功。

转录路径引用仅在本机使用，处理前须保持可读；路径引用不能冻结被压缩重写的转录。处理器在首次存储变更前冻结受限输入，中断重试使用同一份输入。不新增网络请求或客户端命令。队列和隔离文件应按会话数据保护，其文件访问边界与原有本地 hook 捕获相同。

转录读取失败会保留事件、不写完成凭据，并计为可重试失败，不当作空内容处理。单个事件失败后，继续处理后续事件；存储锁或磁盘满等共享故障停止本批。转录缺失在第 3 次读取失败时，或事件已满 7 天且读取仍失败时，进入带原因文件的隔离目录。payload/prepared 的必需字段无效时，在处理前隔离；显式 `skip: true` 是合法的已完成准备状态。

延迟检查点摘要复用普通检查点的来源构造。`source.project_revision` 表示首次准备延迟内容、检查点写入前观察到的项目修订号，不声明它是更早 hook 调用时的修订号。该值与 `source.project_revision_captured_at` 一起冻结在队列事件中，`source.project_revision_capture` 为 `deferred_prepare`；`generated_at` 仍为原始事件时间。项目修订号改变后重试也保留这些值。旧的 prepared 事件在升级后的首次 drain 中采集该来源。

SessionStart 仍同步返回接续简报，使用只读存储访问，并设置固定一秒应用预算。超时或读取失败时输出原有 continue 响应（无上下文），以状态码 0 退出。预算从 Python/模块加载后开始，操作系统启动时间不计入。此路径省略周报提示生成，仍可使用 `engram weekly`。
