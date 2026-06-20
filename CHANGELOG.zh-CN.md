# 更新日志

[English](CHANGELOG.md) | [中文](CHANGELOG.zh-CN.md)

本文件记录 Engram 的所有重要变更。如需带升级说明的详细发布说明，请参阅 [GitHub Releases](https://github.com/Patdolitse/piia-engram/releases)。

格式遵循 [Keep a Changelog](https://keepachangelog.com/)。版本号遵循[语义化版本](https://semver.org/)。

## [4.9.1] - 2026-06-20

### 修复
- 冷启动现在能经 `get_user_context` 兑现"它已经懂我"：自动导入你的 CLAUDE.md / AGENTS.md 规则原本在该工具上被静默挡死——一个非空的"未设置身份"占位串遮住了它的触发条件——导致全新用户拿到的是通用占位提示、而非导入的偏好。现已在首次调用时正常导入，与 `get_resume_brief` 一致。

## [4.9.0] - 2026-06-20

### 新增
- 升级前自动备份：当你的记忆库首次在更新版本的 Engram 下打开时，Engram 会在任何 schema 迁移发生**之前**，把你的数据——经验、身份、Playbook、项目上下文——快照到 `backups/engram-<版本>-<时间戳>/`，这样升级永远不会悄悄丢失或损坏你不可再生的记忆。只保留最近 5 份，每个版本最多触发一次，尽力而为：失败只告警、绝不阻断——除非确有待执行的 schema 迁移，这时它会停下来让你先清理磁盘，而不是无保护地迁移。设 `ENGRAM_NO_AUTO_BACKUP=1` 可关闭。
- Dock GUI 设置页：切换界面语言（中文 / English），一眼看到记忆库的治理 + 遥测状态（只读）。
- Dock GUI 规则与权限页：只读查看你的治理规则和各调用方权限档案。
- `engram dock-playbooks`：Dock GUI playbook 列表的命令行版，与 HTTP 路由共享同一核心。

## [4.8.0] - 2026-06-20

### 新增
- Engram Dock——本地浏览器 GUI。`engram serve --ui` 打开一个仅本地（127.0.0.1）的网页界面来管理你的记忆，不用敲命令：查看/编辑/归档经验与决策（支持批量归档 + 回收站一键恢复）、浏览 Playbook、看记忆库实时概览、一键拿到跨工具「接续」上下文粘到你正在用的 AI 工具里。安装：`pip install "piia-engram[ui]"`。

### 安全
- Dock GUI 安全优先：只绑 127.0.0.1（永不上网），用一次性启动令牌换服务端会话（HttpOnly + SameSite=Strict cookie），对 Host 头做白名单（防 DNS 重绑定），每次写操作都要 Origin + CSRF 令牌，响应无 CORS、带 `no-store` 和 `nosniff`，且永不把记忆库路径发给浏览器。读取零写；被拒绝的写不会打开可写库。

## [4.7.0] - 2026-06-19

### 新增
- 存在感闭环——Engram 每次起作用都可见。保存经验/决策/手册会返回品牌化、带信任层级的确认；`recall` 顶部多一行品牌化信任抬头（`[Engram Recall] N memories · X fresh · Y stale`，只用能证实的新鲜度计数，绝不假装 "verified"）；跨会话简报以 `[Engram] Resumed N memories from <项目> · last session <时间>` 开头。
- 周复盘：新命令 `engram weekly [--json]` 输出过去 7 天的 ≤10 行摘要——新增 lessons/decisions/playbooks 计数、top 领域、画像成长、待审积压，以及一条值得"重现"的旧记忆（确定性挑选，非随机）。SessionStart 每周一次、可忽略的提示提醒你它的存在。

### 修复
- 读取/拒绝写边界：被拒绝的只读外部调用不再产生任何附带写盘（例如 session checkpoint）；新的周复盘读取严格零写。

## [4.6.2] - 2026-06-19

### 新增
- 依赖后继检测：当一条仓库事实的依赖锚点因为该依赖被已知后继取代而失效（例如 `jest` → `vitest`），召回现在会以提示（`superseded_by`）的形式给出这次迁移，而不只是一个"失效"——已不成立的旧事实照常退回未确认的猜测，但你会同时拿到一个"去重新接入后继"的指引。映射是精选且保守的（仅高置信的测试框架迁移），且一旦该事实被重新验证，提示会自动清除。

## [4.6.1] - 2026-06-19

### 新增
- opt-in、content-blind 的首价值漏斗遥测：一个可选的本地信号，把你在 onboard → 可信召回 漏斗里**走到哪一步**（扫描/候选/接受/可信召回/跨工具召回）记成粗粒度的桶——绝不记**你记忆里有什么**。默认关、纯本地（本期不外发任何数据），并**绝对尊重** `DO_NOT_TRACK` / `NO_TELEMETRY`。绝不记录内容、文件名、路径、id、错误原文、持久标识，也**绝不记工具对**。新增 `engram telemetry funnel` 让你看自己的本地漏斗；详见 [docs/telemetry-privacy.md](docs/telemetry-privacy.md)。

## [4.6.0] - 2026-06-18

### 新增
- Dock owner 控制台质量面：新增 4 个本地、owner 运行的命令，供桌面客户端驱动。`engram dock-quality` 和 `engram dock-governance` 是零写汇总——知识质量计数 + 下一条审核 lane，以及治理就绪度 + 各客户端设置覆盖。`engram dock-review-queue` 按 lane 列出待审条目。`engram dock-quality-action` 执行单条 owner 确认动作（validate / promote / archive）：默认只预览，加 `--yes` 才写；promote 只作用于 staging 条目；archive 可恢复。四个命令都只返回元数据——不含标题、正文、推理或路径。

## [4.5.1] - 2026-06-18

### 修复
- `engram recall` 现在会为每条仓库支撑的事实显示 owner 信任块——为什么可信、它的锚点、上次验证时间、衰减策略——让"可信召回"这一首次价值在命令行就能看到，而不只在 MCP 客户端里。加 `--no-trust` 可隐藏。

### 新增
- 批量接受：`engram onboard-accept --all` 一条命令接受当前仓库的全部 staging 候选。默认先预览（会接受多少条、因锚点失效拒绝多少条、作为跨仓库跳过多少条），加 `--yes` 才写入。每条仍逐一对锚点校验，因此批次可部分成功，且绝不接受别的仓库的候选。

## [4.5.0] - 2026-06-18

### 新增
- onboard-repo 首次价值路径：`onboard_repo` 扫描本地仓库，生成带仓库 provenance 的 staging 候选知识；owner 再通过 `onboard_accept` / `accept_onboard_candidate` 接受候选，已接受的仓库事实可在 private-self owner recall 中带着 trust 信号返回。
- CLI 与 MCP 外壳：本地 CLI 支持扫描与接受流程，高级 owner-only MCP 面新增 `onboard_repo` 与 `onboard_accept`，默认 Tier-1 工具集保持不变。
- 验收证明目标：scan -> staging candidate -> owner accept -> trusted recall 的确认流程面向代表性仓库设计为 <=10 分钟内完成。

### 修复
- `accept_onboard_candidate` 现在会拒绝推广 `anchor_project_id` 与目标仓库解析出的 project id 不一致的候选，并保留其 staging 状态，避免跨仓库 provenance 被误信任。

## [4.4.0] - 2026-06-18

### 新增
- 来源感知的新鲜度：`compute_freshness` 现在先读每条知识的来源，再据此选择衰减策略。人工确认的事实仍走时间衰减；测试/锚点确认的事实是触发式的，不再受时钟驱动而变 stale。公开的 4 态 `freshness_status` 不变；来源感知以附加信号 `skip_decay` / `decay_policy` 叠加，由衰减、刷新、stale 呈现等路径遵守。
- owner 确认盖章：`confirm_knowledge` 记录显式确认来源（human、test 或 anchor）。agent 无法自证；每条写入路径都会剥离信任字段。
- 锚点读时校验器（`check_anchors`，owner 触发）：把一条事实绑定到仓库里可观察的锚点（某个依赖或文件），以项目的 git-remote 身份解析。锚点被判失效（依赖没了）时，把这条事实降回 guess（tier→staging、清除确认、保留 invalid 锚点状态作为证据）；查不出结果（unresolvable）则只回退到时间衰减，所以“查不了”不会被当成“它没了”。降级是单向的：依赖回来也不自动恢复信任，需刻意重新确认。

## [4.3.0] - 2026-06-17

### 安全
- 加固私有路径脱敏（更广的模式覆盖），并清除 fixture/代码中泄漏的本地路径。
- worker 反馈载荷改为服务端校验，并守护公开 worker 配置，避免 Cloudflare worker 配置泄漏。
- 收窄可信本地（trusted-local）写入：可信本地 agent 仍可直接写入低风险的新经验/决策，但高影响操作（改身份、覆盖既有知识、改关系）改为需 owner review。

### 新增
- `doctor` 与状态报告中可选地呈现治理状态。
- 知识的系统派生 provenance/标注元数据，并在 recall/staging/review 间打通标注验证闭环（标注由系统派生、仅作参考，agent 无法自设）。
- 经验版本快照（知识版本链），编辑时保留历史。
- Dock M1：只读的后端状态契约（`dock-status`）——零写、仅元数据。

## [4.2.0] - 2026-06-15

### 新增
- **只读零写入存储模式。** `Engram(read_only=True)` 现在保证真正的零写打开：
  字段迁移、信任边界回填、会话戳、更新提醒全部被抑制，客户端可以零副作用地
  读取存储。`engram preview --read-only` 在此保证下渲染记忆透视报告。
- **面向本地桌面客户端的只读 CLI。** 一组零写命令，用于拉取可直接粘贴的上下文
  而不改动存储：`dock-resume`（接续简报）、`dock-search`（关键词搜索）、
  `dock-list`（列出全部活跃经验/决策，供「我的记忆」一次性浏览）、
  `dock-portrait`（完整样式的用户画像 HTML）、`dock-archived`（列出已归档条目）、
  `dock-get-lang`（读取 owner 当前语言）；另有 `dock-export` 提供一键完整备份
  （会写出备份文件）。
- **从本地客户端管理 owner 知识。** `dock-onboard-scan` / `dock-onboard-commit`
  从粘贴文本或选定的项目文件夹（最近 git 提交 + README）提取经验/决策候选，
  供 owner 确认、可编辑后导入；`dock-update` 编辑条目内容；`dock-archive` /
  `dock-restore` 可逆地归档与恢复条目。
- **更丰富的用户画像 HTML。** 画像新增工作风格、知识构成、协作工具、"已陪伴
  N 天"，以及点击展开查看完整经验/决策内容的下钻。
- **Owner 语言切换。** `dock-set-lang` 写入 profile 的语言（zh/en），桌面客户端
  可一处切换自身界面与 Engram 的画像、隐私预览、CLI 输出的语言。

### 修复
- 加固 `read_only`，堵住若干惰性写入路径（字段迁移、原子写、信任边界回填），
  确保只读打开真正无副作用。
- pre-commit hook 现在选用可用的 Python 解释器，而不是 Windows Store 占位入口。

## [4.1.0] - 2026-06-12

### 新增
- **决策冲突治理。** 冲突检测在 `doctor`、上下文组装与召回三处收敛为同一套
  阈值；版本演进链（supersedes 关系）不再被误报为冲突。新增 owner 专用 CLI：
  `engram conflicts list [--json]` 只读视图，以及 `engram conflicts resolve
  --action supersede|archive|dismiss`（默认只预览；落盘需 `--commit --yes`）。
  `doctor` 现在给出最值得处理的冲突对样本（含 id 与分数），dismiss 抑制记录
  随原生导入导出一并迁移。
- **MCP 工具面 capability modes。** `ENGRAM_TOOLS` 在 `core` / `all` 之外
  支持按能力组合（`knowledge`、`governance`、`admin`、`integrations`，
  `+` 号拼接）；未知 token 安全回退到 core 并给出双语警告。`engram setup`
  新增工具面选择器（全部 53 / 仅核心 17 / 核心+知识库）——写入的默认值保持
  不变——`doctor` 会显示当前生效模式。指南见
  [docs/operator-mcp-cheatsheet.md](docs/operator-mcp-cheatsheet.md)。
- **`engram preview`（记忆透视 Memory Lens）。** 本地双语 HTML 报告，
  展示模拟 AI 调用方会收到的完整内容——身份卡、注入上下文，以及每条内容
  背后的治理决策。
- **供应链可验证性。** 发布流水线现在为发布的 wheel/sdist 产物生成 SBOM
  与 GitHub artifact attestations。自行验证方法见
  [docs/supply-chain.zh-CN.md](docs/supply-chain.zh-CN.md)
  （[English](docs/supply-chain.md)）。

### 修复
- 清零随 v4.0.0 发布带出的文档漂移（过期工具数与版本头），并让漂移守卫
  登记式排查既往漂移模式，确保同类回归无法再次发布。

### 文档
- README 新增 CI/守卫状态徽章与"自行验证"路径，链接公开守卫脚本与
  release-evidence 索引。

## [4.0.0] - 2026-06-11

### 变更
- **破坏性：MCP 工具面从 87 个合并为 53 个。** 相关操作族合并为带
  `mode`/`action` 选择器的单一工具；旧名在同一版本中直接移除（不设过渡别名）。
  默认加载的 17 个 Tier-1 核心工具保持不变。要点：身份读取 →
  `get_identity_facets`；Playbook 读取 → `get_playbooks`（`mode`）；Playbook
  管理 → `manage_playbook`；执行 → `playbook_execution`；staging 评审 →
  `review_staging`；知识关系 → `manage_relation`；关联探索 →
  `explore_knowledge`；决策线程并入 `get_decisions`（`thread_seed_id=` /
  `history_question=`）；画像 → `user_portrait`；调用方信任 →
  `manage_caller_trust`；OpenClaw 导入导出并入 `export_engram` /
  `import_engram`（`format="openclaw"`）；批量写入并入
  `memory_store`（`items_json=`）。完整老名 → 新调用映射与行为说明见
  [docs/migration-v4.zh-CN.md](docs/migration-v4.zh-CN.md)。
- **破坏性：旧 Playbook 作用域迁移移出 MCP**，改为 owner 专用本地 CLI：
  `engram playbook scope classify|apply|rollback|queue|resolve`（默认只预览；
  落盘需 `--apply --yes`）。
- **staging 评审门收紧**——`review_staging` 对包括 `list` 在内的所有 action
  先过写入门（旧 `list_pending_staging` 是只读类）；read-only-external 调用方
  现在连 `list` 也会被拒。owner 与 trusted-local 调用方不受影响。

### 文档
- 新增迁移指南 [docs/migration-v4.zh-CN.md](docs/migration-v4.zh-CN.md)；双语 README、
  用户指南、架构文档、operator 速查表、工具面分析与注册表元数据中的工具表格
  与数量全部刷新。

## [3.56.0] - 2026-06-11

### 新增
- **Claude Code Watcher 适配器**——通用 Watcher 现在可以从磁盘转录
  （`~/.claude/projects/`）捕获 Claude Code 会话。当 Claude 设置中已接线
  Engram Stop hook 时适配器自动让位，hook 用户绝不会被重复捕获；它只作为
  未接 hook 环境的兜底通道。
- **setup 内置增强检索开关**——`engram setup` 新增可选步骤，一键启用 hybrid
  检索（关键词 + 全文 + 语义向量）：提供 `[vector]` 依赖安装选项，把
  `ENGRAM_SEARCH=hybrid` 写入已检测 AI 客户端的 MCP 配置，并在 setup 收尾
  构建索引。重跑 setup 不再静默丢失客户端配置里已启用的 `ENGRAM_SEARCH`。
  hybrid 仍为默认关闭、自愿开启。

### 修复
- **Hook 失败不再静默**——Claude Code 和 Cursor 各 hook（停止保存、压缩吸收、
  续接简报注入、写回）依旧绝不阻塞宿主工具，但被吞掉的失败现在会在
  `<ENGRAM_DIR>/logs/hooks.log` 留下一行痕迹（有大小上限），与 Watcher 已有的
  `watcher.log` 可观测性对齐。

### 文档
- 混合检索现在可以从 README 和用户指南发现
  （`pip install "piia-engram[vector]"` + `ENGRAM_SEARCH=hybrid`），并新增
  混合检索指南的中文版。
- `docs/architecture.md` 追平 v3.55：新增"捕获通道"章节，记录 `hooks/` 与
  `watcher/` 两个子包及其契约；模块表更新到 v3.55 巨石拆分后的形态
  （`knowledge_ops` / `playbooks` / `tools_registry` / `doctor` /
  `cli_commands` / `search_index`）。

### 变更
- **Watcher 增量捕获**——Watcher 的检查点现在只携带上次成功保存之后新增的
  对话轮次（状态文件按文件记录字节 offset），不再每次重发整个转录的重叠
  尾部。长会话的 context 日志不再堆积重复内容。已有 Watcher 状态自动迁移；
  转录被重写/轮转时检测到并从头重读；写到一半的末行绝不消费（等写完整再收）。
- **内部结构重组（API 无变化）**——三个最大的模块按职责拆分以提升可维护性：
  `core.py` 拆出 `playbooks.py` / `tools_registry.py` / `knowledge_ops.py`
  mixin；`setup_wizard.py` 拆出 `doctor.py` / `cli_commands.py`；
  `mcp_server.py` 拆出五个 `mcp_tools_*.py` 模块。所有公开入口、MCP 工具面
  （87 个工具）与导入路径均保持不变。

## [3.55.0] - 2026-06-10

### 新增
- **通用会话 Watcher**（`piia_engram.watcher`）——后台轮询器，为不支持 hooks 的
  AI 工具自动捕获会话到 Engram contexts。内置 Codex 适配器（解析 rollout JSONL
  转录）。水位线增量扫描、按文件防抖、首跑不回灌历史、只写 contexts（绝不触碰
  知识库）。
- **一键自启安装**——`engram watcher install` 在 Windows 上配置按用户登录自启
  （启动文件夹快捷方式 + 无控制台启动器；无需管理员权限）。配套子命令：
  `start`、`status`、`uninstall`、`once`。非 Windows 平台输出 cron/systemd 指引。
- **历史召回触发规则**——四个支持工具的指令片段现在会告诉模型：用户问起历史
  对话时调用 `get_recent_context`。

### 修复
- **PyPI 发布门禁解堵**——v3.54.0 的 PyPI 发布被拦截：公开边界清理移除了
  `release-evidence/` 跟踪，而 CI 发布门禁仍依赖它。证据文件以"纯标记声明"
  形式重新跟踪（不含内部测试数或扫描明细）；详细记录留在本地。v3.54.0 从未
  发布到 PyPI，其变更随本版本一起发布。

## [3.54.0] - 2026-06-10

### 新增
- **Cursor 会话 hooks**——`sessionStart` 时自动注入续接摘要，`stop` 时自动保存会话上下文。
  适配 Cursor 真实协议（环境变量传输 `CURSOR_TRANSCRIPT_PATH` / `CURSOR_PROJECT_DIR`；
  stdin 为空）。带角色前缀的富转录提取、超长文件尾部读取、按会话 UUID 防抖、优雅降级到
  每日最小检查点。36 个新测试。
- **冷启动 Playbook 触发匹配**——`get_user_context` 现在在用户提示词命中触发关键词时
  自动浮现相关 playbook，无需手动查找。
- **推送后 GitHub 状态探针**（`scripts/post_push_closeout.py`）——只读
  `--github-status` 模式，push 后检查 CI / 发布状态但不触发任何操作。

### 修复
- **P0 安全：信任字段夹带漏洞关闭**——MCP 入口层现在剥离调用方自带的 `tier`、
  `approval_status` 字段；信任文档对齐。
- **并发压力测试诚实化**——Windows 锁超时偶发误判改为"无静默丢失"契约。
- **解密失败显示安全**——`ENGRAM_SECRET` 错误或缺失时，读取路径返回用户可见占位符
  而非抛异常。

### 变更
- **公开内容边界强制执行**——55 个内部文件（发布证据、runbook、计划、设计文档）
  从 git 跟踪移除并 gitignore。
- **`proposed_only` 写策略改名为 `direct_write`**，标签诚实化。

## [3.53.0] - 2026-06-08

### 新增
- **诚实头对头对比文**（`docs/honest-comparison.md` + `.zh-CN.md`）——中英双语叙事版
  对比 vs mem0、Basic Memory、ByteRover。每条竞品声明标日期、脚注竞品自己的公开文档
  （快照 2026-06-08）。含"对方赢在哪"、"我们更弱的地方"和诚实选型指南。
  为 `docs/comparison.md` 的配套深度版。

### 变更
- **定位重写（README 第一屏）**——以治理开头（"看得见、可改可删、可覆盖"），连续性退为
  底座。诚实纠偏：从所有公开文案、元数据、守卫测试中移除过度承诺用语（"只记你确认过的"
  "记什么由你说了算"等）。
- **已发布元数据诚实纠偏**——`.mcp/server.json`、`pyproject.toml`、
  `.claude-plugin/plugin.json`、`.cursor-plugin/plugin.json` 描述改为诚实能力框架
  （"AI 建议；高风险先等你审查；全部可见可逆"）。
- **守卫测试反转**——`test_public_positioning.py` 现断言诚实措辞，禁止过度承诺短语。
- **许可证从 Apache-2.0 变更为 AGPL-3.0-or-later。** 开源核心仍是可自由使用、
  修改、自部署的自由软件；联网或托管形式的再分发须以相同许可证开放对应源代码
  （AGPL 第 13 条）。3.52.0 及更早版本仍为 Apache-2.0，本变更自后续版本起生效。

## [3.52.0] - 2026-06-08

### 新增
- **新写入按风险分级的审批门（N3）**：新写入的经验/决策会先做风险评估。低/中风险
  条目自动吸收为 `verified`（approved，写审计），下次会话立即可用；只有高风险条目
  （凭证、shell 命令、权限规则、MCP 配置）才进 staging 待人工审。无人监督的回写钩子
  （如 Cursor）无论如何强制 staging，LLM 自动提取的条目不能自标 `verified`，
  调用方显式 pin 的 tier 会被尊重。`get_resume_brief` 在 handoff 顶部浮现
  `pending_review` 计数（含高风险条数）。
- **空库首次连接自动引导（auto-bootstrap）**：对空库的首次 `get_user_context` /
  `get_resume_brief` 会只读扫描已有规则文件（CLAUDE.md / AGENTS.md / .cursorrules）
  并导入，省掉一步手动设置。通过 `.bootstrap_done` 哨兵保证最多执行一次。
- **setup 语言选择**：`engram setup` 现在以数字选项的 中文 / English 语言选择开场，
  向导全程渲染双语文案，不跟随系统 locale。
- **Context Governance 提案型能力**：新增本地 proposal-only 的上下文治理辅助能力，
  覆盖 context usage report、role-scoped recall、Safe Context / Lockdown
  转换、freshness / conflict 提案、上下文压缩 replay packet，以及外部证据页面草稿。
  这些能力不会发布、push、tag、修改已存知识，也不会自动执行归档决策。
- **Context Governance 预览入口**：新增 advanced MCP 工具
  `preview_context_governance`，作为 safe-context、freshness/conflict、replay
  和 evidence 提案的统一 owner-gated 预览入口。

### 变更
- **setup 外部配置默认改为"确认后写入"**：`engram setup` 现在会检测受支持的客户端、
  列出将要修改的具体配置文件路径，并仅在一次按键确认后才写入 MCP 连接（每次写前先备份）。
  选"否"则一字不改。`--apply-external-config` 保留为跳过确认的非交互 / CI 路径。
- **公开信任叙事对齐新行为**：README（中英）、SECURITY、trust 文档和双语 quickstart
  现在如实描述"确认后写入"的外部配置流程与按风险分级的审批模型，同时保留真正成立的
  保证（写前备份、拒绝=零改动、ledger 路径脱敏、高风险仍需人工批准）。
- **MCP 工具面语义收口**：在公开文档、技能引用、Glama 元数据、CLI help 和 MCP
  docstring 中统一说明：Tier-1 / core 表示"高频入口、节省上下文预算"，不等于
  "只读安全集合"。owner export、owner/admin、可选本地集成、internal/dogfood
  和 legacy maintenance 工具面现在都有明确标注。

### 修复
- **工具面漂移保护**：新增测试锁定 owner/export 与 owner/admin schema 标记、
  `get_identity_card` 的 core-but-export 分类、本地工具图谱分类，以及 legacy
  Playbook 维护工具分类。
- **公开事实刷新**：当前本地事实更新为 3045 passed、2 skipped、3047 collected，
  MCP 工具数为 87 个（17 core / 70 advanced）。

## [3.51.2] - 2026-06-06

### 变更
- **Playbook 被动引用契约**：将已保存 Playbook 归一化为带版本的结构契约，
  保留 pitfalls / preconditions 等 prose 字段，暴露执行 outcome 汇总，
  增加可选 `required_tools` 工具依赖声明，并把 MCP `usage_policy` 运行时提示
  扩展到 `search_knowledge` 命中的 Playbook，让主复用路径保持治理语义。
- **Playbook 与工具图谱桥接**：Playbook 现在可用 canonical `required_tools`
  元数据声明本地工具需求，同时保留 `tool_refs` 作为输入别名。
  `prepare_playbook_execution` 会在运行时通过工具图谱解析可用性，返回
  `resolved_tools`、`tools_ready` 和 `missing_tools`，但不会把本机绝对路径写入
  Playbook 或 execution plan。

### 修复
- **工具版本未知状态**：当已登记工具没有可判断版本时，现在会显式返回
  unknown / unsatisfied 状态，而不是误报满足 `min_version` 要求。
- **公开事实刷新**：当前本地事实更新为 2906 passed、2 skipped、
  2908 collected。

## [3.51.1] - 2026-06-05

### 新增
- **公开信任证据页**：新增 `docs/trust-evidence.md`，把隐私 / 信任声明映射到
  确定性本地检查，并明确说明这些证据不能证明什么，方便外部技术读者审阅。
- **首价值 quickstart**：新增 `docs/quickstart-first-value.md`，用默认核心工具完成
  从安装、首次批准记忆，到新会话召回的最短路径。
- **MCP 工具面分析**：新增 `docs/tool-surface-analysis.md`，作为 analysis-only
  文档梳理 83 个 MCP 工具、17 core / 66 advanced 分层、功能簇和治理类别。

### 修复
- **公开信任声明漂移 guard**：新增 `scripts/check_public_trust_claims.py`、测试和
  CI / publish 接线，确保 README、SECURITY、PRIVACY、telemetry 文档中的网络、
  遥测、明文、可选加密和 endpoint 口径保持一致。
- **CI-like pytest 入口 guard**：新增本地检查，提前捕捉 `PYTHONPATH=src` 下测试
  导入 `scripts.*` 导致 GitHub Actions 失败的问题。
- **公开工具数漂移**：修正残留的 64 / 81 工具数表述，统一为当前 83 total、
  17 core、66 advanced。
- **同名项目区分**：澄清 `Gentleman-Programming/engram` 是无关项目，产品形态不同。

### 变更
- **公开事实刷新**：当前本地事实更新为 2881 passed、2 skipped、2883 collected。
- **发布流程可见性**：release orchestrator 拆分为 `prep` 和 `publish-fast` 模式，
  让本地证据检查与真正发布的热路径分开。

## [3.51.0] - 2026-06-05

### 新增
- **Recall Eval v1 基线**：新增确定性、合成、公开安全的 recall benchmark，
  用真实 `search_knowledge` 结果和期望 knowledge ID 打分，不使用真实用户存储、
  网络调用或 LLM judge。
- **Admission Guard v1**：新增只读候选记忆入库闸，将质量判断、重复检测和明显冲突
  路由合并成 metadata-only 动作：`accept`、`stage`、`reject`、`duplicate`、
  `review_update`。
- **Held-out Memory Eval Suite**：新增独立 held-out recall/admission fixture 和
  `scripts/run_memory_evals.py`，让记忆质量在发布前可以用聚合指标复现和引用。

### 修复
- **Admission 冲突漏判**：`without user confirmation` 这类发布授权否定短语现在会
  路由到冲突 / update review，不会直接作为新 lesson 接受；同时保留
  `without downtime` 作为非冲突部署表达，避免误报。
- **Eval CLI 源码漂移**：recall/admission eval 脚本现在优先加载当前工作树 `src/`，
  避免直接 CLI 运行时误导入已安装的旧包。
- **发布 workflow guard 覆盖**：CI、publish workflow 和 release orchestrator
  都已接入 memory eval suite gate，并用红灯测试证明该 gate 遇到退化期望会失败。

### 变更
- **公开事实刷新**：当前本地事实更新为 2865 passed、2 skipped、
  2867 collected。
- **暂缓 ranking/search 优化**：held-out suite 未暴露真实 recall failure，
  因此本轮没有改动 recall ranking/search。

## [3.50.0] - 2026-06-05

### 新增
- **动态生成导出脱敏 gate**：新增 release / CI guard，会构造合成 Engram 数据，
  真实生成 identity card、knowledge report 和 AGENTS export，再扫描生成物中是否
  存在 secret、路径、邮箱等泄漏形状。
- **只读 staging 队列工具**：新增 `list_pending_staging`，作为 reviewer / GUI
  可用的 metadata-only MCP 读取工具；`batch_review_staging` 的实际 mutation
  仍然受 write governance 约束。
- **Telemetry vNext 与 dashboard 文案对齐**：继续加固本地优先、默认关闭的
  telemetry contract，并让 dashboard 可见文案匹配当前能力边界。

### 修复
- **导出 metadata 脱敏**：domain label、source-tool label、section header、
  lesson summary/detail、decision 文本、stale title 和 related title 现在都会在
  identity card、knowledge report、AGENTS export 渲染前经过脱敏。
- **发布 guard 覆盖面**：release orchestrator、CI workflow 和 publish workflow
  都已接入动态生成导出脱敏 gate，不再只依赖静态 clean sample。

### 变更
- **公开事实刷新**：当前本地事实更新为 2830 passed、2 skipped、2832 collected；
  MCP 工具数为 83 个：17 个 core，66 个 advanced。
- **OpenClaw 公开边界保持收窄**：OpenClaw 仍只声明 L3 static file bridge
  静态文件桥证据；不声明 live OpenClaw agent / model continuity。

## [3.49.2] - 2026-06-05

### 新增
- **Cursor 写回 hook（默认关闭、只进 staging）**：新增受保护的 Cursor
  stop-hook 入口。默认不启用，带重入保护、transcript 大小上限，并统一通过
  `extract_session_insights` 写入 staging review，不会自动变成 verified 知识。
- **批量 staging 审查 MCP 工具**：新增 `batch_review_staging`，用于批量预览或
  审批/拒绝 staging lesson / decision。默认 dry-run，真正改写必须显式
  `confirm=true`，返回内容保持 metadata-only。
- **连续性与兼容性证据信号**：刷新 MCIC / client-continuity 相关证据和兼容脚手架，
  让 Hermes / OpenClaw 证据继续保持可复现、边界清楚，只基于 copied-store 或静态桥运行。

### 修复
- **乱码修复误报**：普通 ASCII 问号和正常中文不会再被误判为可修复乱码。现在只有
  Unicode replacement character 这类真实损坏符号才作为 repairable-loss 证据，
  原有强 mojibake marker 仍然保留。
- **Permission Profile vNext staging 边界**：`staging_optin` 不能再覆盖
  review / publish 阶段的 staging 排除规则，staging 可见性继续受生命周期阶段约束。
- **Doctor MCP 入口探测**：用测试锁定 setup wizard 对 MCP entry shape 的分类和
  有界 `--help` 探测行为。

### 变更
- **公开事实刷新**：当前本地事实更新为 2782 passed、2 skipped、2784 collected；
  MCP 工具数为 82 个：17 个 core，65 个 advanced。
- **OpenClaw 公开边界保持收窄**：OpenClaw 仍只声明 L3 static snapshot A/B
  静态文件桥验证通过；不声明 live OpenClaw agent / model continuity。

## [3.49.1] - 2026-06-05

### 修复
- **治理账本完整性检查**：`integrity` 现在会正确解包 ledger verify 返回的
  `(ok, message)`，不再把非空 tuple 当成天然通过。被篡改或损坏的治理账本会被报告为
  unhealthy，而不是静默通过。

### 安全
- **身份卡导出脱敏**：导出的 identity card 现在会在渲染前清理 lesson 摘要和
  decision question/choice 中的凭证形状、裸邮箱和用户主目录绝对路径，避免已存知识里的敏感片段进入可携带导出面。
- **遥测信任文档纠偏**：`SECURITY.md`、`CONTRIBUTING.md`、
  `docs/architecture.md` 和 `docs/comparison.md` 现在与 `PRIVACY.md` 使用同一套分层遥测口径：
  local telemetry 先写本地，remote telemetry 和 feedback 是独立显式 opt-in，
  且不收集身份正文、知识正文、项目路径、自由文本或错误栈。
- **测试数据边界文案**：客户端验证 runbook 和历史计划文档现在使用 isolated run root、
  isolated Engram dir、owner desktop 等占位路径，不再固化真实本机路径。

### 新增
- **公开主张漂移扫描**：`scripts/check_public_claim_drift.py` 会扫描当前跟踪的
  Markdown 文档，发现过期测试数、工具数和过度宣传语；历史 release evidence 会被显式跳过。
- **导出脱敏 linter**：`scripts/check_export_redaction.py` 可扫描已渲染导出文本，
  以 metadata-only 结果报告高置信 secret / PII 形状，不回显原始敏感值。
- **发布流程 dry-run 编排器**：`scripts/release_orchestrator.py` 提供本地只读发布清单，
  在真正远程发布前提前暴露 GitHub、PyPI、MCP Registry、Glama/manual 等授权和卡点。
- **确定性证据 harness**：新增离线合成证据脚本，覆盖客户端 A/B 信号、版本链确定性、
  召回排序复现、备份恢复 round trip、只读管理面、store integrity 故障、MCP schema drift、
  离线安装矩阵和有界并发压力测试。

### 变更
- **遥测边界测试**：新增 sealed-network 测试，证明默认关闭时 telemetry 不写本地 log、
  不尝试远程请求；远程 telemetry 仍然必须由用户显式 opt-in。
- **公开事实刷新**：当前本地事实更新为 2743 passed、8 skipped、2751 collected。

## [3.49.0] - 2026-06-04

### 新增
- **显式导入版本链实体化**：完整备份导入现在支持
  `engram import <backup.json> --apply --yes --materialize-version-chain`，
  用于 owner 确认后的同 key 知识冲突落链。dry-run 已标记为
  `review_version_chain_candidate` 的分歧 lesson / decision 会被导入为新的
  active 条目，写入 `supersedes` 边，并把旧条目标记为 `outdated`。默认
  `--apply --yes` 合并行为保持保守，不会实体化冲突；返回结果仍是
  metadata-only。
- **MCIC v1 基准**：新增 `demos/mcic_benchmark.py`，提供 10 个带测试目的的
  合成、metadata-only 多客户端身份连续性场景。覆盖显式召回、隐式个性化信号、
  对抗假前提防护信号、公开动作边界、版本链 HEAD 选择、负控和 provenance
  往返。该基准的主张刻意收窄：Engram 让下一个客户端拿得到信号；真实模型是否
  会照做仍需要单独 A/B 测试。
- **客户端验证证据脚手架**：新增 `src/piia_engram/client_validation.py`
  和 `scripts/run_client_validation.py`，把 Hermes、OpenClaw、Cursor
  以及后续 MCP host 的复制数据目录测试标准化。脚手架会记录测试目的、
  隔离的来源/目标路径、零污染 hash 证据和公开安全的双语摘要，并通过主张闸门
  避免把未经验证的 live client 行为写成已通过。

### 变更
- **MCP 启动同步延迟**：启动对账现在默认在 daemon 后台线程中执行，避免 stdio 客户端在 MCP initialize 阶段被本地 AI 记忆/配置扫描阻塞。设置 `ENGRAM_MCP_STARTUP_SYNC=eager` 可恢复旧版同步启动行为；设置 `ENGRAM_MCP_STARTUP_SYNC=off` 可在延迟敏感验证臂中跳过启动同步。stdio 启动下的 `auto_migrate()` 仍保持同步执行；启动对账与 MCP 写工具共享进程内写锁，避免启动期读-改-写 JSON 更新互相覆盖。
- **Cursor 插件显示名**：Cursor 插件 manifest 的显示名改为
  `piia-engram`，让本地插件界面与包名、MCP Registry 身份保持一致。

## [3.48.3] - 2026-06-04

本地导入候选版本。Engram 将完整备份的导入/导出逻辑从核心引擎中拆出，并新增更安全的 owner 导入预览路径。默认 `engram import <backup.json>` 仍然只读、只返回 metadata-only 预览，只有显式使用 `--apply --yes` 才会改写本地数据。

### 新增
- **Owner 导入预览 CLI**：`engram import <backup.json>` 默认执行 metadata-only dry-run；真正导入必须使用 `--apply --yes`，`--overwrite` 映射到 replace-mode import。
- **语义冲突预览**：dry-run import 现在会把同 summary 的 lesson、同 question 的 decision 中语义字段不同的候选标记为 `review_version_chain_candidate` 冲突，但不会写入版本链边，也不会改变已存知识。

### 变更
- **导入/导出拆分**：完整备份 export/import 逻辑迁移到 `ImportExportMixin`，缩小核心引擎文件表面，并为后续版本链实体化工作留出更清晰的边界。

### Release Evidence
- 见 `release-evidence/v3.48.3.md`。

## [3.48.2] - 2026-06-04

OpenClaw 兼容桥接加固补丁。OpenClaw 兼容的 `MEMORY.md` 导出现在只包含
verified 且 active 的知识，并加入保守的字节预算，避免静态文件桥把 staging /
pending review 内容带给外部客户端，也避免快照无限膨胀。

### 修复
- **OpenClaw `MEMORY.md` 导出边界**：`export_to_openclaw` 现在只导出
  `tier=verified` 且 `status=active` 的 lesson / decision。staging、pending、
  archived、rejected 或非 active 条目默认不会进入静态桥接文件。

### 变更
- **静态桥尺寸保护**：`MEMORY.md` 输出限制为 32 KiB，并裁剪过长 summary /
  reasoning，让 OpenClaw 兼容快照对外部客户端保持可读且边界清晰。
- **客户端验证文档**：新增 purpose-first 验证 runbook，覆盖 Cursor Agent、Hermes、
  OpenClaw 兼容流程和未来 MCP host 的证据要求、负控和零污染检查。

## [3.48.1] - 2026-06-04

性能补丁——在检索热路径上记忆化分词。无行为变化：搜索输出（token 集合、别名
扩展、排序）完全一致；不改 API、schema、遥测、治理或权限。

### 变更
- **分词缓存**——`_tokenize` 现在委托给进程级 `@lru_cache` 的纯函数，缓存键为
  `(text, expand_aliases)` 与导入期即静态的别名表。此前热路径在每次查询时对相同
  条目字段反复分词；记忆化后将这部分重复 CPU 计算折叠为一次字典查找。全量语料的
  热查询关键词搜索中位数 ~53ms → ~20ms（−62%）。缓存值为不可变 `frozenset`——
  只读消费者（`_score_item` 字段求交、`_bigram_similarity`）直接使用，需要修改的
  调用方（如 `_score_item`）获得新的 `set` 副本。

## [3.48.0] - 2026-06-03

本地产品批次——所有者确认的 apply 通路与就绪度展示。全部为 CLI / 仅所有者、
仅元数据；不新增面向 Agent 的 MCP apply 工具，不改遥测 schema，不改权限/治理，
不做硬删除，也不对外发布。

### 新增
- **产品使用流程加固**——`engram merge --json` 现在返回与 `engram merge apply`
  相同的仅元数据 dry-run apply 负载，预览 JSON 不再回显建议摘要或存储正文。
- **对账冲突预览 v2**——`engram reconcile conflicts [--json]` 仅展示冲突计数与
  匹配 id。只读、仅元数据，绝不导入、取代或覆盖既有决策。
- **GUI 安全的所有者操作**——`engram dashboard --json` 新增 `next_action` 及
  `actions` 列表（含 code/label/command/count/risk 与 `executes=false`），为未来
  UI 提供可安全渲染的元数据，不引入一键改动。
- **遥测控制台密码轮换助手**——`scripts/rotate_telemetry_dashboard_password.ps1`
  生成或接受 shell 安全的 `DASH_PASSWORD`，先打印供所有者交接，仅在带 `-Apply`
  时才写入 Cloudflare Worker secret。
- **近重复合并 apply（N4）**——`engram merge` 列出仅元数据的近重复建议；
  `engram merge apply` 预览/折叠，复用既有可逆的 `merge_knowledge` 软归档
  （次条标记 `outdated`/`merged_into`，绝不硬删除）。默认 dry-run，
  `--commit --yes` 才真正应用。新增 `src/piia_engram/merge_apply.py`。
- **对账导入 apply（N2）**——`engram reconcile` 将外部 AI 记忆候选分类
  （import / duplicate / conflict / skip）；`engram reconcile apply` 仅导入新颖
  （`import`）候选。重复与冲突仅作为元数据 no-op 展示，绝不改动既有知识
  （冲突→supersede 解析推迟）。默认 dry-run，`--commit --yes` 才导入。新增
  `src/piia_engram/reconcile_apply.py` 与只读 `Engram.collect_memory_candidates()`。
- **版本链 HEAD 展示（N5）**——新增 `version_chain.head_ids()` 及仅渲染的标注：
  召回输出新增 `meta.version_chain`（collapsed/heads_present），续接简报在存在被
  取代的版本链时给出提示（召回/控制台呈现当前 HEAD）。
- **控制台就绪度计数（D）**——`engram dashboard` 新增仅元数据的 `readiness` 区块：
  生命周期、对账、近重复合并、版本链 HEAD 的待所有者确认 apply 计数。

### 变更
- README 与 README.zh-CN 当前状态测试数更新为已验证基线：2469 通过、8 skipped、
  共收集 2477。

## [3.47.1] - 2026-06-03

公开事实同步补丁：Engram 新增机器可读的 public facts manifest，并把公开事实漂移检查接入 CI 和 PyPI publish workflow，防止 README、manifest、工具数、测试数和版本号在发布前再次漂移。

### 新增
- `docs/public-facts.json`：当前开发树的单一事实源，记录版本、测试数、MCP 工具分层和 telemetry 默认状态。
- `scripts/check_public_fact_sync.py`：公开事实漂移检查，已接入 CI 和发布流程。
- `scripts/count_mcp_tools.py`：通过 AST 确定性计算 MCP 工具数，不导入包、不产生副作用。
- `docs/runbooks/public-truth-sync.md`：说明 dev truth / released truth 的边界、远端 registry 只在发布时更新，以及实时核验清单。

### 变更
- README 和 README.zh-CN 当前状态表更新为已验证基线：2415 passed、8 skipped、2423 collected。
- 本地 release-build runbook 将 public fact sync guard 纳入发布前检查。

### Release Evidence
- 见 `release-evidence/v3.47.1.md`。

## [3.47.0] - 2026-06-03

遥测收尾版本：Engram 完成 Telemetry Analysis Contract v1/v1.1 闭环，补齐本地就绪校验、仪表盘分析卡片、远端 D1/Worker 收口证据，以及明确的 dashboard 访问控制说明。遥测仍然是 opt-in；不会收集身份、项目路径、prompt、知识正文或自由文本内容。

- **`engram telemetry-validate --remote-readiness`** —— 纯只读的部署前检查清单（payload↔schema 映射、worker 事件/反馈白名单、两个迁移文件、v1 先于 v1.1 的顺序、仪表盘"匿名日 ID"措辞 + v1.1 分桶卡片、客户端默认 opt-out、无内容字段）。不执行任何网络/D1/部署动作。
- **仪表盘 v1.1 分析卡片** —— worker 仪表盘新增 v1.1 派生分桶（版本采纳、知识激活、匿名回访分桶、错误趋势），以 v1.1 迁移为前提渲染，并标注为匿名日 ID 分桶（绝不声称"独立用户"）。
- **整合远端收尾 runbook** —— 单一规范流程（校验 → v1 迁移 → v1.1 迁移 → 部署 → 健康检查 → 冒烟 → 校验 → 清理 → 回滚），主机/数据库用占位符；v1 与 v1.1 runbook 交叉链接到它。
- **遥测隐私证据**（`docs/telemetry-privacy.md`）—— 明确的 opt-in / 无内容 / 轮换日 ID / 远端激活需用户授权声明，并补充 dashboard `DASH_PASSWORD` 边界。
- **本地 worker 冒烟测试** —— 静态测试锁定三层 insert（完整 v1.1 → v1 回退 → legacy）、内容字段拒绝、仪表盘标签，并在 `worker/test/` 下附可选 node 执行 harness。
- 修复 `parse_added_columns` 把 SQL 注释里的词误读为列名的潜在 bug。

### 运维
- 经用户明确确认后，远端 D1 schema 已迁移到 19 列，`engram-telemetry` Worker 已部署，临时冒烟事件验证 P0/P1 落库后已删除，并已为线上 dashboard 设置 `DASH_PASSWORD`。
- 清理后远端遥测真实计数：12 条事件、2 个匿名日 ID 分桶。

### 测试
- 遥测就绪检查：`READY`（9/9 项通过）。
- 遥测 Python 测试：122 passed。
- Worker/v1.1 测试：26 passed。
- Worker smoke harness：全部通过。

### Release Evidence
- 见 `release-evidence/v3.47.0.md`。

## [3.46.0] - 2026-06-03

信任与就绪版本：Engram 把"记忆信任闭环"产品化，并新增一整套本地、增量、仅提案（proposal-only）的就绪面（phase 6-13），不改变任何默认行为、不触碰远端状态。

### 新增
- **记忆信任闭环** —— recall 现在端到端携带来源（provenance）与新鲜度（freshness）信号。新增 `recall`、`quality_eval`、`reports_review` 表面，把 provenance/freshness 契约接入 MCP server，使检索到的知识可以连同来源和过期程度一起展示，且不改变默认排序。
- **Recall 与版本链表面** —— 新增 `recall_service`、`version_chain` 模块，在本地暴露确定性 recall 和知识版本链投影；由 recall 质量与 recall/version 端到端审计覆盖。
- **`engram backup-plan` CLI** —— 在升级前预览本地 Engram 数据的 metadata-only 备份计划，不执行任何破坏性动作。
- **`engram export-agents-md` CLI** —— 从本地知识为非 MCP 工具导出 `AGENTS.md` 身份/上下文文件，owner 授权。
- **生命周期 / 完整性 / 冲突调和 安全面** —— 新增 `lifecycle`、`integrity`、`reconcile_proposal` 模块及 `engram lifecycle`、`engram integrity` CLI，产出仅提案（proposal-only）预览（衰减/规模、自诊断、冲突调和），自身永不改写已存储知识。
- **Owner 控制面** —— 新增 `engram dashboard`（owner_dashboard）、`engram release-check`（release_readiness）、`engram telemetry-validate`（telemetry_validation）本地 CLI，以 metadata-only 投影汇总状态、发布证据就绪度和遥测载荷有效性。
- **跨工具连续性 harness** —— 新增 `continuity_harness` 模块与语料，提供本地、确定性的检查，确认 resume/连续性输出在不同工具间保持一致。
- **Permission Profile vNext（只读脚手架）** —— `permission_profile_vnext` 落地权限档模型与预览；read-gate 强制执行接线仍处于 gated 状态，默认不启用。

### 变更
- **遥测分析契约 v1.1（本地分桶）** —— 选择性开启的本地遥测载荷现在包含 v1.1 派生分桶（版本采纳、激活状态、回访分桶、错误趋势），传输层 `schema` 保持不变，所有取值均为短、无时间戳的分桶。
- Owner/management 投影继续只汇总计数与状态，不打印本地项目路径或已存储知识正文。

### 安全
- **send_feedback 边界加固** —— 反馈/遥测发送边界现在运行 fail-closed 拒绝名单与字段校验，确保没有自由文本知识正文离开本地发送边界；极短、有歧义的 CJK token 被拒绝，作为可接受的残余风险。
- **发布 / 公开边界加固** —— 刷新 publish allowlist，并保持定位文案诚实（不声称远端部署、真实同步、Cursor 实时 hook 或 read-gate 强制执行），使公开包表面与实际交付一致。

### 测试
- 全量套件：**2327 passed**，8 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 新增审计：provenance 接线、recall/version 端到端、proposal 正确性/确定性、feedback 拒绝名单、遥测契约 v1.1、lifecycle/integrity/reconcile、owner dashboard、发布就绪、连续性 harness、以及 permission-profile-vNext 预览。

### 未执行（用户把关）
- 本次发布准备不执行任何远端 Cloudflare Worker / D1 迁移、PyPI 上传、MCP Registry 或 Glama 更新、GitHub Release、tag 或 push。
- Permission Profile vNext read-gate 强制执行、Cursor 实时 stop-hook 回写、真实多设备同步仍为"已设计但 gated"，未启用。

### Release Evidence
- 见 `release-evidence/v3.46.0.md`。

## [3.45.3] - 2026-06-01

公开边界纠偏版本：Engram 从公开包表面移除一个内部内置 Playbook 模板，并把构建后 artifact 私有词扫描加入发布硬闸。

### 修复
- 从源码、CLI help、status 输出、README 命令示例、更新日志措辞和 release evidence 中移除内部内置 Playbook 模板；公开包只描述通用 Playbook 引擎能力。
- status 与 management 表面继续保持 metadata-only，不对外展示维护者工作流模板。

### 安全
- 新增 release artifact 私有词扫描脚本：构建 wheel / sdist 后解包扫描生成 metadata、README 副本、打包测试和包内文件，并使用 gitignored 的维护者私有规则。
- PyPI publish workflow 现在使用 internal strict 源码脱敏扫描，并在发布前以 strict 模式运行 artifact 私有词扫描。
- release evidence 现在要求 `artifact-private-scan` 标记，使包体级私有词扫描成为 CI 强制发布门禁。

### Release Evidence
- 见 `release-evidence/v3.45.3.md`。

## [3.45.2] - 2026-06-01

CI 入口补丁版本：Engram 现在让 cross-tool resume benchmark 测试同时适配 GitHub Actions 使用的 `pytest` console-script 入口，而不只依赖本地 `python -m pytest`。

### 修复
- 在 `tests/test_cross_tool_resume_benchmark.py` 增加显式 repo root 导入保护，修复 `pytest` 在只把 `src` 放入 `PYTHONPATH`、但没有把仓库根目录放入 `sys.path` 时的 CI 收集失败。

### 测试
- 全量套件：**2020 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- CI 形态回归：`pytest tests/test_cross_tool_resume_benchmark.py -q` 在不设置 `PYTHONPATH` 时通过。

### Release Evidence
- 见 `release-evidence/v3.45.2.md`。

## [3.45.1] - 2026-06-01

CI 打包补丁版本：Engram 现在把 `demos` 命名空间作为可导入包处理，使干净 CI checkout 在 Linux、macOS、Windows 上都能稳定收集 cross-tool resume benchmark 测试。

### 修复
- 为 `demos/` 增加 package initializer，修复 CI 收集 `tests/test_cross_tool_resume_benchmark.py` 时的 `ModuleNotFoundError: No module named 'demos'`。

### 测试
- 全量套件：**2020 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单完整，package build + twine check 通过。

### Release Evidence
- 见 `release-evidence/v3.45.1.md`。

## [3.45.0] - 2026-06-01

提取质量与管理工作流版本：Engram 现在更可靠地过滤短期提醒，保留带指标结果的工程发现，并为未来 GUI 提供更安全的 metadata-only 管理投影。

### 新增
- **指标型提取信号**：自动提取现在能识别延迟下降、百分比、耗时变化、回归等 measured outcome，让具体工程发现更容易进入待审核记忆。
- **短期提醒过滤**：tomorrow / send / email / call / remind 等短期个人任务默认不会进入长期记忆，除非同时带有可复用证据或指标结果。
- **Playbook 迁移影响摘要**：旧 Playbook 作用域 apply / rollback 预览现在返回只含元数据的影响计数、目标 scope 分布、跳过原因计数和确认状态，不暴露 Playbook 标题、正文、步骤或项目路径。
- **GUI-ready 管理过滤**：`engram management` 与 `build_management_view()` 支持按 review 类型/质量、Playbook 状态/scope 过滤，同时保持 metadata-only。

### 变更
- 管理文本和 JSON 输出继续只汇总计数与状态，不打印本地项目路径或已存知识正文。
- Claude 验收改为窄口径只读审计，由 Codex 记录本地测试证据，降低复杂发布检查中的超时风险。

### 测试
- 全量套件：**2020 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单完整，package build + twine check 通过，Claude 验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.45.0.md`。

## [3.44.0] - 2026-06-01

安装文件安全版本：Engram 现在在 setup 阶段默认只读外部 MCP 客户端配置，允许用户选择 Engram 数据目录，并在替换 Engram 自有 JSON 存储前保留备份。

### 新增
- **可选择 Engram 数据目录**：setup wizard 现在允许用户选择默认本地目录、其他磁盘或自定义 Engram root；生成的客户端配置会携带该 `ENGRAM_DIR`。
- **文件安全备份 ledger**：Engram 自有写入在替换既有 JSON 存储前会创建带时间戳的备份；ledger 只记录元数据和相对 Engram root 的路径。
- **管理动作 CLI**：`engram management action request|approve|reject|complete|list` 记录只含元数据的管理 receipt，便于后续 UI 提供用户可控的清理与审核流程，同时不打印知识正文。

### 变更
- setup 默认不再改写外部 AI 客户端配置文件。用户可以用 `--apply-external-config` 显式选择写入；dry-run 和普通 setup 会保持外部文件字节级不变。
- `engram doctor --fix` 和显式 setup 配置写入现在都会在触碰外部配置前走统一备份与 ledger 路径。
- 旧 MCP 配置里的自定义 `ENGRAM_DIR` 在 repair 或升级时会被保留，除非用户明确选择新的数据目录。

### 修复
- `auto_migrate()` 现在把旧 JSON/TOML MCP 客户端配置当作只读迁移提示，不再在 import-time startup 中静默修改。
- storage 更新现在会在原子替换既有 Engram 自有 JSON 文件前保留备份。

### 测试
- 全量套件：**1994 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单完整，package build + twine check 通过，Claude 验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.44.0.md`。

## [3.43.0] - 2026-05-31

连续性诊断版本：Engram 现在提供可分享的 metadata-only 交接证明、recall-loop 计数，以及更清晰的 Windows 编码诊断。

### 新增
- **仅含元数据的连续性证明**：`engram continuity` 报告已保存 session 数、参与工具、resume brief 构建状态、context-load / wrap-up 聚合信号和跨工具就绪状态，不打印 memory 正文、raw telemetry events、session IDs、decision reasoning 或本地完整路径。

### 修复
- `engram repair-encoding` 现在会说明 clean scan 代表存储的 Engram 数据是健康的；如果 UTF-8 文件在 Windows/PowerShell 中仍显示乱码，应检查终端显示编码。
- `engram status` 现在会在 CLI 由绝对路径启动且 console-script 目录不在 `PATH` 时，回退查找同目录的 `piia-engram-mcp` launcher，避免 Windows/Codex runtime 场景中的 MCP entry 误报。

### 测试
- 全量套件：**1834 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单完整，release evidence 完整，Claude 验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.43.0.md`。

## [3.42.0] - 2026-05-31

信任、接续与可携带性基础版本：Engram 现在提供更安全的恢复诊断、更强的跨工具接续简报、信任元数据基础字段，以及只含元数据的配置完整性检查。

### 新增
- **恢复保留 dry-run**：`engram recover-json lessons` 现在会为有效恢复候选输出不含正文的 retention plan，包含 overlap / union / overflow 计数和建议，不恢复 live store，也不打印 lesson 正文或 raw IDs。
- **30 秒接续简报**：`get_resume_brief()` 现在以 compact handoff 开头，包含项目、最近活动、下一步动作和 trust note，提醒 AI memory 只是参考上下文，不是新的用户授权。
- **Trust Mode 元数据基础**：lessons / decisions 现在携带派生的 `memory_state`、`approval_status`、`provenance`、`risk_level`、`risk_flags`、`approval_required`，同时保留既有 `tier` / `status` 兼容。
- **配置完整性诊断**：终端 `engram doctor` 现在报告 MCP config、AI instruction、shared instruction、Claude hook 和 project rule 的 metadata-only counts / hashes。

### 变更
- Trust metadata 由服务端派生且单调收紧：调用方不能自提 staging、压低 high-risk flags，或关闭 high-risk approval。
- `get_resume_brief()` 和非 `--fix` doctor continuity 检查读取 lessons / decisions 时不会隐式 backfill 旧格式 knowledge 文件。
- 文档现在如实说明 lessons / decisions 当前仍有 access-based staging promotion path，playbook review 仍保持显式确认。

### 修复
- 加固入口 help 与编码读取：CLI help 避免初始化副作用，Windows UTF-8 输出可靠解码，JSON 读取接受 UTF-8 BOM。
- 修复 Codex/MCP 配置生成：即使测试或 setup 运行在 Linux/macOS 上，也能从 Windows 风格源码路径推导正确的 `PYTHONPATH`。
- 关闭调用方通过 `memory_state`、`risk_level`、`risk_flags`、`approval_required` 与服务端派生状态冲突造成的 trust-field 自降级边界。
- 关闭非 `--fix` doctor 构建 resume brief 时可能改写旧格式 knowledge 文件的副作用。
- 修复 staging -> verified promotion 后派生 trust metadata 仍黏在 `staging/pending` 的问题。

### 测试
- 全量套件：**1826 passed**，1 skipped，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单完整，package build + twine check 通过，Codex subagent 复审 PASS，Claude Code 只读验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.42.0.md`。

## [3.41.0] - 2026-05-31

市场定位与信任说明版本：Engram 现在更精确地表达为面向 MCP 兼容 AI 编程工具的本地优先个人 AI 身份层，并补齐信任边界与公开跨工具连续性 demo。

### 新增
- **信任模型文档**：`docs/trust.md` 说明哪些内容留在本地、默认不会发生什么、治理边界、用户控制项和已知局限。
- **跨工具连续性 demo**：`docs/cross-tool-continuity-demo.md` 与 `demos/cross_tool_continuity_demo.py` 使用隔离临时 Engram root 展示 Claude Code -> Codex -> Cursor/Windsurf 的模拟交接。
- **渠道文案包**：`docs/listing-copy.md` 提供 MCP Registry、Claude plugin、PyPI、GitHub 和网站表述的保守公开文案。
- **公开定位回归测试**：`tests/test_public_positioning.py` 防止旧过度承诺、MCP Registry 描述超长、demo 泄露本机路径、公开文档漏加发布白名单。

### 变更
- README、中文 README、PyPI 元数据、MCP Registry 元数据、Claude 插件元数据、架构说明和竞品对比统一为“面向 MCP 兼容 AI 编程工具的本地优先个人 AI 身份层”定位。
- `docs/comparison.md` 现在把 Engram 与 OpenMemory、原生编码工具记忆区分开，而不是把它表述成通用 agent memory 数据库。
- 中文 README 安全说明补充可选 agent 治理环境变量，并明确它不是加密身份认证边界。

### 测试
- 全量套件：**1788 passed**，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单全覆盖，MCP Registry manifest valid，Codex subagent 审计 PASS，Claude Code 只读验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.41.0.md`。

## [3.40.0] - 2026-05-31

首次运行信心版本：Engram 现在在安装后提供更清晰的本地状态界面，包括 MCP 客户端配置健康、可分享的脱敏 HTML 状态页，以及后续常用命令。

### 新增
- **`engram status` MCP 客户端摘要**：CLI 现在用脱敏元数据报告哪些客户端已配置、哪些缺少配置。
- **更完整的状态 HTML**：`engram status --html` 现在包含 MCP Clients 表格和 Next Commands 区块，提示 `engram doctor`、`engram review`、`engram sessions`。
- **状态探测测试覆盖**：新增对 MCP 入口有界探测和 `status --help` 输出的回归测试。
- **Codex + Claude 验收流程**：本地项目流程文档记录了 Codex 实现、Claude 验收的协作闭环。

### 变更
- `engram status --html` 将 Engram 存储路径渲染为 `<engram-root>`，方便把 HTML 作为脱敏证据分享而不暴露本机用户路径。
- `scripts/release_sanitize_check.py` 的用户可见消息改为 ASCII，避免 Windows 终端出现 mojibake。
- README、中文 README、架构说明、隐私示例、MCP Registry 元数据和 Claude 插件元数据同步到 v3.40.0。

### 修复
- 关闭状态 HTML 通过文本状态块嵌入本机 Engram storage path 的泄漏边界。
- 新增回归测试，确保 status HTML 不包含 MCP 配置路径、entry args/env、token 或本机 Engram root。

### 测试
- 全量套件：**1781 passed**，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单全覆盖，Codex subagent 审计 PASS，Claude Code 只读验收 PASS。

### Release Evidence
- 见 `release-evidence/v3.40.0.md`。

## [3.39.1] - 2026-05-30

终端编码诊断补丁版：Engram 现在能帮助用户区分真实存储数据乱码与 Windows / 终端显示编码问题。

### 新增
- **`engram doctor` 终端编码诊断**：CLI doctor 现在会分别报告 stdout/stderr 编码、`PYTHONIOENCODING` 和 Python 运行时编码，并与存储数据的 `Encoding health` 分离。
- **Windows UTF-8 代码页支持**：将 `cp65001` 识别为 UTF-8，避免 `chcp 65001` 的 Windows 终端被误报为 legacy 编码。

### 变更
- 当 stdout/stderr 已经是 UTF-8 时，`engram doctor` 会把未设置 `PYTHONIOENCODING` 视为 OK，减少健康终端里的误导性提示。
- README、中文 README、cross-tool guide、架构说明和隐私示例同步到 v3.39.1。

### 修复
- 修复“Engram 数据本身干净，但终端显示层 Unicode 渲染异常导致用户误判数据损坏”的诊断盲区。

### 测试
- 全量套件：**1767 passed**，4 个预期内 `engram_core` 改名兼容 warning。
- 发布门禁：脱敏 high=0/warn=0，发布白名单全覆盖，Claude Code 只读复审 PASS。

### Release Evidence
- 见 `release-evidence/v3.39.1.md`。

## [3.39.0] - 2026-05-30

本地工作流可见性版本：Engram 现在为已保存会话、暂存知识审查和治理边界提供更清晰的本地入口，同时不改变默认本地优先的隐私模型。

### 新增
- **会话连续性 CLI**：`engram sessions` 只用元数据列出跨工具保存的 AI 会话，`engram sessions show <id>` 只打印用户明确指定的单个会话。
- **doctor 连续性检查**：`engram doctor` 新增 Continuity 段，区分干净新安装的“尚无保存会话”和真正的 resume brief 构建失败。
- **暂存知识审查 CLI**：`engram review`、`engram review show <id>`、`engram review approve <id> --yes`、`engram review archive <id> --yes` 为暂存 lessons / decisions 提供终端审查、确认和归档路径。
- **治理文档**：新增公开治理文档，说明调用方信任等级、读/写/导出门控、文件副作用加固、语料加密边界和 deny-by-default 工具矩阵。

### 变更
- README、中文 README、架构文档和隐私文档现在统一为 16 个核心工具 / 56 个高级工具 / 72 个 MCP 工具总数，并同步 `enc:v2` 加密表述、会话存储路径、playbook 存储路径和当前 CLI 入口。

### 测试
- 全量套件：**1762 passed**，4 个预期内 `engram_core` 改名兼容 warning。

## [3.38.0] - 2026-05-30

### 新增
- **编码修复防线**——新增 `engram repair-encoding` CLI，默认 dry-run 扫描当前 Engram root 中 JSON / JSONL / Markdown / text 文件里的高置信 mojibake。`--apply` 会先建时间戳备份，再修复可逆内容；已经丢字节的可疑内容只报告给人工复核，不盲猜。
- **doctor 编码健康检查**——`engram doctor` 现在包含 "Encoding health" 段，`engram doctor --fix` 可在常规自诊断流程中一并修复可逆乱码。

### 修复
- **Windows stdio UTF-8 加固**——MCP server 启动时会把 stdout/stderr 切到 UTF-8，避免 Windows GBK/CP936 默认控制台编码污染 MCP JSON 帧或中文输出。
- **写入入口文本归一化**——lesson、decision、playbook、profile、project snapshot、saved context 等写入路径会在持久化前修复高置信 mojibake，同时保守跳过正常中文。

### 测试
- 新增可逆 GBK mojibake 修复、正常中文不误修、不可逆可疑内容报告、Markdown context 修复、`doctor` 集成、MCP stdio UTF-8 启动等回归测试。
- 全量套件：**1742 passed**，4 个预期内 `engram_core` 改名兼容 warning。

## [3.37.0] - 2026-05-30

GUI 入口采用版本：piia-engram 现在提供更容易粘贴到各类 GUI AI 工具里的通用 MCP server 命令，setup wizard 也覆盖了两个新的 home 级 MCP 客户端。

### 新增
- **`piia-engram-mcp` console 入口**——MCP 客户端现在可以用一个命令启动服务器，不必写完整的 `python -m piia_engram.mcp_server`。旧的模块路径仍然可用，并调用同一个 `main()` 函数。
- **零安装 MCP 配置写法**——README 示例现在说明 `uvx --from piia-engram piia-engram-mcp`，适合用户不想预先 `pip install`、只想在客户端里粘贴命令的场景。
- **Trae 与腾讯 CodeBuddy setup 支持**——`engram setup` 可以写入它们的标准 home 级 MCP 配置文件（`~/.trae/mcp.json` 与 `~/.codebuddy/mcp.json`）。
- **国产 AI IDE 接入文档**——README / README.zh-CN 现在区分可自动配置的工具，以及通义灵码、文心快码、Qoder 这类 UI 管理或项目级配置工具。

### 测试
- packaging 测试现在钉住新的 `piia-engram-mcp` entry point，并验证它能解析到可导入的 callable。
- setup-wizard 测试现在钉住 Trae 与 CodeBuddy 配置路径，防止未来重构时静默丢掉这些 GUI 入口。
- 全量测试：**1720 passed**。

## [3.36.0] - 2026-05-30

身份层安全版本：知识正文静态加密、每个 AI 工具内联看到自己的权限边界、治理层对"写绕过"和"读路径副作用"双向封死。治理与加密两条线各自经过多轮独立（Codex）对抗式审计，单是读路径闭合就走了五轮。

### 新增
- **语料静态加密（a5）**——知识正文字段（`summary`、`detail`、`question`、`choice`、`reasoning`、`title`、`description`、`outcome`）用预派生密钥（PBKDF2-SHA256 600K + 每个 engram 独立的 `.corpus_salt`）加每字段随机 AES-GCM nonce 加密，采用新前缀 `enc:v2c:`。元数据保持明文，搜索和过滤照常工作。向后兼容：明文条目透明放行，下次写入时惰性重加密。playbook 复合字段（steps / pitfalls / preconditions）、playbook 索引标题、执行计划派生文件（含 step notes）全部覆盖。
- **调用方权限内联呈现（a1–a3）**——AI 工具从第一条消息起就能知道自己的治理状态、信任级别、敏感度上限和写策略，无需额外 MCP 调用：`get_user_context` 和 `get_resume_brief` 追加"Caller Permissions"段，`search_knowledge` / `get_relevant_knowledge` 结果带 `_caller_permissions` 键。
- **`TOOL_GOVERNANCE_CLASS`**——对每个 `@mcp.tool` 的 deny-by-default 分类。反射测试会对任何既未分类也未显式豁免的工具亮红灯，未来漏挂门控的工具无法静默发布。
- **`maybe_refuse_owner_write`** 治理辅助函数，用于 owner-only 写/导出前置门控。

### 安全
- **写路径治理门（a4）**——18 个写工具（`add_lesson`、`add_decision`、`add_playbook`、`memory_store`、`update_knowledge`、`archive_knowledge`、`review_knowledge`、`merge_knowledge`、`link_knowledge`、`unlink_knowledge`、`update_playbook`、`archive_playbook`、`update_identity`、`register_tool`、`save_project_snapshot`、`start_project`、`save_agent_context`、`update_execution_step`）在调用方写策略为"no"（read-only-external）时执行前就拒绝。owner 与 trusted-local 调用方放行。
- **读路径对非 owner 零磁盘副作用（R5–R9）**——治理开启时，`read-only-external` / 低信任调用方不再能通过任何读类工具触发文件写入。此前一次"被拒绝"的读，仍可能在拒绝*之前*已经落盘。已闭合的面：知识读取时的 access-count / `last_reviewed` 写回、遥测（`_track` flush 与 `_beta` 事件文件）、`audit.log` 记录、以及 `contexts/mcp_auto/*` 会话检查点（后者仅在调用次数越过阈值后才触发，单次调用的测试因此漏过了它）。
- **权限管理工具加 owner-only 前置门控**——`set_caller_trust`、`revoke_caller`、import 现在在任何副作用之前就拒绝，低信任调用方无法靠"先写 grants 再被治理"自我提权。
- **`get_identity_card` 重分类为 export-owner-only**——它的磁盘导出现在按写入面门控，不再当作普通读。
- **所有治理门控 fail-closed**——若 owner 解析抛异常（grants 损坏、导入失败），副作用被抑制而非放行。门控的失败模式是"拒绝"，不是"放行"。
- **加密 fail-closed 加固**——存在任何既有密文（按完整文件内容扫描，含派生索引/执行文件，不止前 4KB）时若缺 `.corpus_salt`，则让 engram 打开失败而非新铸一个 salt；语料密钥激活时清除残留的明文 `search_index.db`，使"开启加密"不会被遗留索引架空；语料加密下整个混合搜索索引被抑制，防止明文落进 FTS 表。

### 变更
- **发布闸强制 R1/R5 自测准入规则**——`release-evidence/v<版本>.md` 现在除 `eval-gate` 外还要求两个 presence-only 标记：`negative-control`（R1：安全敏感改动的新回归测试必须被证明在修复前的代码上变红）和 `field-assertion-audit`（R5：被触碰的安全敏感模块里每个自由文本字段都要有落盘断言证明它不是明文写入）。两者须为 `passed` 或 `n/a`。这把 a5 审计中学到的纪律固化下来——当时"我写的测试全过"掩盖了四个明文泄漏 P1。
- CLI `engram reindex` 现在报告"corpus encryption enabled; persistent search index skipped/purged"，而非误导性的"reindexed 0"。

### 测试
- **治理写门控矩阵：166 个测试**——writer-spy 全 root 快照比对、对"读工具 × 客户端类型"的反射式 sweep（每个调用重复到越过遥测/检查点阈值）、root 外路径监控（伪造 `HOME`/`TEMP`）、以及 fail-closed 错误路径证明（owner 解析抛异常时仍须零写入），并配 owner 对照测试防止过度修正。每个门控都有 revert-to-RED 证明钉住：每个修复被确认在移除后会让对应回归测试变红。
- 语料加密和调用方权限工作在 a1–a5 及 Codex 审计各轮中累计新增约 115 个测试，每个都在修复前的 commit 上做了 R1 negative-control 证明。
- 全量套件：**1718 passing**。

## [3.35.0] - 2026-05-29

决策链完善、决策修订历史、权限档案——首个让用户能追踪决策演变过程并控制谁可以访问 Engram 数据的版本。

### 新增
- **决策链自动取代（c1）**：`add_decision` 现在在同一问题得到不同答案时自动创建 `supersedes` 边。支持显式 `supersedes` 参数进行跨问题取代。去重比较器修复（`>` → `>=`），相似度平局时最新条目优先。
- **`remove_relation` MCP 工具**：`add_relation` 的撤销操作——移除知识条目间的类型化关系。幂等。
- **`get_decision_history` MCP 工具（c2）**：按问题文本（非 ID）查询决策的完整修订历史。返回时间线排序的修订列表、取代链和当前有效决策。使用二元组相似度匹配，阈值可配。
- **权限档案（a0）**：三个用户可控的治理 MCP 工具：
  - `get_permission_profile`：查看所有调用方的信任等级、自动分类规则和已撤销列表
  - `set_caller_trust`：设置或修改调用方的信任等级（private-self / trusted-local / read-only-external）
  - `revoke_caller`：前向撤销调用方的未来访问权限

### 变更
- MCP 工具总数：65 → **72**（16 个 Tier-1 核心 + 56 个 Tier-2 高级）。
- README "量化数据"刷新至 v3.35.0（1439 测试，72 工具，16 核心）。

### 测试
- 新增 50 个测试：决策链 c1（14）+ c2（15）+ 权限档案（21）。
- 完整套件：**1439 个**测试通过。

## [3.34.0] - 2026-05-29

治理层（a0）、决策链脚手架（c0）、playbook 被动参考 header——首个带运行时信任执法和产品级"AI 不自动执行"安全属性的版本。

### 新增
- **治理层（a0，需手动开启）**：设置 `ENGRAM_GOVERNANCE=1` 启用运行时信任执法。非 owner 调用者（不受信任的 `web` 层级）无法读取、导出或派生超出其信任上限的存储知识。全部 65 个 MCP 工具按 deny-by-default 分类：受治理（返回过滤）、仅 owner 导出（写前拒绝）、或安全白名单（已注释）。默认关闭——不开 flag 零行为变化。
- **Playbook `usage_policy` header**：MCP 工具返回的每个 playbook 和执行计划现在都带有 `usage_policy` 字段，指示消费方 AI 将其视为被动参考——逐步与用户确认后再执行，不得自动驱动决策或一键跑完。应用于 `get_playbook`、`get_playbooks`、`get_recent_playbooks`、`prepare_playbook_execution`、`get_execution_status`。
- **决策链脚手架（c0）**：`add_relation` 和 `get_decision_thread` MCP 工具——知识条目间的类型化/有向关系与链路重建。为未来决策链可追溯性奠基。
- **敏感度自动分类**：零配置安全分类器，基于内容启发式分配 `public` / `work` / `secret`。供治理门使用，治理关闭时可安全忽略。

### 安全 / 加固
- **治理层 a0 读路径切换——经 Codex 独立复审 6 轮（R15→R20）**：
  - R15：未知信任层级 fail-closed + 接入所有知识体读取
  - R16：全工具 deny-by-default 覆盖（不再依赖名称前缀启发式）
  - R17：`refresh_quick_context`、`get_identity_card`、`export_knowledge_report` 文件副作用门
  - R18：`prepare_playbook_execution` 写前门（执行计划文件泄漏）
  - R20：非 owner 抑制 hybrid 搜索索引（`search_index.db` FTS 表泄漏）
- **通用文件副作用 harness**：参数化回归测试覆盖全部 41 个受治理/导出工具，大小写无关内容比对，覆盖断言（新工具不在 harness 中自动失败），反验证明（证明 harness 确实能抓到真实泄漏）。
- **哈希链治理审计账本**：仅追加的 `governance_ledger.jsonl`，SHA-256 链用于防篡改检测。

### 变更
- 仓库文档采用英文规范策略（国际化通过独立文件）。
- 新增 LobeHub 市场徽章和 Awesome-MCP-ZH 收录。

### 发布证据
- Codex 独立复审：R20 PASS（a0 读路径全切换，含写回显 + 导出门 + 去重回显 + 审计日志 + 文件副作用门 + 混合索引门）。
- 完整套件：1385 个测试通过。治理专项：215 个测试。
- eval-gate：n/a（无检索算法变更）。

## [3.33.2] - 2026-05-28

一批由独立代码审查（Codex）发现并修复的正确性 / 安全问题——这是首个完整通过全部三道关卡的版本：「自审 + 独立 Codex 审查 + 评估关卡」。

### 修复
- **混合检索召回保证**：启用混合检索时，RRF 重排序 + 截断可能把关键词命中的条目挤出 top-N。现在关键词结果（score ≥ 阈值的 top-`limit`）始终予以保留，再通过 RRF 回填，确保混合检索的召回率 ≥ 关键词检索的召回率。
- **安装 `[vector]` 后索引未重建**：最初在没有向量后端的情况下只构建了 FTS 索引；安装 `[vector]` 依赖后，之前不会触发重建，语义信号始终缺失。现在「向量后端可用性」已成为索引新鲜度指纹的一部分，当启用向量但向量表缺失时会强制重建。
- **pre-commit 密钥扫描 `--staged` 漏报**：之前读取的是工作区文件内容，因此如果一个含密钥的文件被 `git add` 后，工作区被清理且未重新 add，实际被提交的密钥就会被漏掉。`--staged` 现在扫描暂存区的 blob（`git show :path`）。
- **pre-commit 白名单 `--staged`**：`.publishallow` 现在从暂存区读取，未暂存的本地改动不再影响提交决策（hook 标记 v2→v3）。

### 安全 / 加固
- **发布工作流加固**：`publish.yml` 移除了 `workflow_dispatch`（一个可从未受保护分支手动触发的绕过面），并在发布前验证发布 commit 是 `origin/main` 的祖先。同时建议在 GitHub 仓库的 Environment 设置中添加部署分支限制。

### 发布证据
- 三道关卡全部通过：自审 + 独立 Codex 复审（round-2，commit dcd8621，全部 6 项确认已修复）+ round11 评估关卡 PASS；完整套件 1022 个测试通过。

## [3.33.1] - 2026-05-28

混合检索补丁：一个在代码审查中发现的索引新鲜度修复。

### 修复
- **更换嵌入模型后索引未重建**：当 `ENGRAM_EMBED_MODEL` 变更（或默认模型升级）但知识内容保持不变时，之前的索引新鲜度指纹只统计内容、不统计模型，因此不会触发重建——旧维度的向量表残留，向量信号被静默禁用（KNN 维度不匹配 → 被吞掉 → 返回空结果），直到内容变化或手动运行 `engram reindex` 为止。现在嵌入模型已成为指纹的一部分，更换模型即触发重建。
- 非向量索引不再写入 `embed_model` 标记，避免误报维度漂移标志。

## [3.33.0] - 2026-05-28

混合检索现已正式可用，需手动开启，默认行为不变。

### 新增
- **混合检索（需手动开启）**：在既有关键词检索之上，融合 FTS5 全文检索与可选的语义向量层，通过 Reciprocal Rank Fusion（k=60）合并排名。通过 `ENGRAM_SEARCH=hybrid` 启用；**默认仍为关键词检索，行为完全不变**。
  - 语义层通过 `pip install piia-engram[vector]` 安装（sqlite-vec + FastEmbed），默认模型为 **BAAI/bge-small-zh-v1.5**（中文优先，可通过 `ENGRAM_EMBED_MODEL` 覆盖；更换模型会自动重建向量，且维度变化时不报错）。
  - 索引是可重建的 SQLite 文件，**JSON 仍是唯一可信源**（删除索引即可从 JSON 完整重建），支持惰性重建（仅当内容指纹变化时重建）+ 增量向量嵌入。
  - FTS5 现在执行 CJK 二元分词，中文不再被当作单个 token。
  - 新增 `engram reindex` 命令用于手动重建索引。

### 验证
- A/B 评估关卡（关键词 vs 混合）通过：中文集零召回回退（recall@5 1.00→1.00，MRR 在容差内），且**跨语言召回（英文查询 → 中文知识）从 0.50 提升到 0.875**——这是关键词检索在结构上无法做到的。

### 发布证据
- 完整回归套件 1005 个测试通过。

## [3.32.0] - 2026-05-28

发布工作流加固的后续：将发布前的安全检查提前一步。

### 变更
- **pre-commit hook 现在也运行发布白名单检查**：由 `python scripts/install_git_hooks.py` 安装的 pre-commit hook，除密钥扫描外，还会验证所有暂存内容都在 `.publishallow` 白名单之内——新增一个未登记的被跟踪文件现在会在提交时被拦截，而不必等到 CI。（可用 `git commit --no-verify` 临时绕过。）

### 安全 / 加固
- **密钥扫描器新增多行扫描**：之前只逐行匹配，因此跨行换行的内部叙述（例如在 docstring 内被换行符分隔）会被漏掉；现在对 `.py` / `.md` 等文本文件额外执行整文件扫描，只报告真正跨行的命中，且不与逐行结果重复。

### 发布证据
- 完整回归套件通过。

## [3.31.0] - 2026-05-28

跨工具自动续接完善 + 知识层级管理 + 发布工作流加固。

### 新增
- **跨工具会话续接**：Cursor / Codex / Windsurf 的指令片段现在都提示 AI 在会话开始时调用 `get_resume_brief` 以续接上一轮工作，与 Claude Code 的 SessionStart hook 行为一致。新增对 Windsurf 的支持。
- **可选的 pre-commit 密钥关卡**：通过 `python scripts/install_git_hooks.py` 安装后，每次提交前自动扫描暂存区的敏感内容（可用 `--no-verify` 临时绕过）。

### 变更
- **`update_knowledge` 支持调整层级**：一条知识可在 `staging` / `verified` / `archived` 之间直接移动，无需「归档旧条目 + 新增一条」的两步操作；层级变更会写入审计日志。
- **PostCompact hook 职责收窄**：命令 hook 现在只把压缩摘要归档到日志，而语义提取（lesson/decision）统一由 agent hook 处理，消除重复写入。
- **doctor 检测过期指令片段**：能识别缺少跨工具续接指令的旧片段，并在 `--fix` 时刷新。
- **README 改用官方 Glama 质量徽章**（动态评分，替换手写徽章）。

### 安全 / 加固
- 发布守卫的具体路径模式移至本地 `.guardignore`（不入库），公开工作流只保留通用类别。
- 整合了对比文档中的存储规模说明，避免反复强调上限。
- 收紧发布内容控制：从黑名单升级为默认拒绝的发布白名单（`.publishallow` + CI 验证），并引入公开 / 内部双轨 CHANGELOG。
- 密钥扫描器新增内部信息泄露模式检测（审查代号、模型代号等）。

### 发布证据
- 完整回归套件通过。

## [3.30.1] - 2026-05-27

修复 `engram doctor --fix` 无法升级过期 hook 的问题。

### 修复

- `engram doctor --fix` 现在能正确升级遗留的 Claude Code hook 配置（例如将指向 `scripts/*.py` 脚本路径的旧式写法升级为当前的 `python -m piia_engram.hooks.*` 形式）。此前 doctor 的严格匹配检查报告「缺失」，但 `--fix` 的幂等跳过逻辑认为它「已注册」而跳过——导致用户陷入「doctor 说缺失、--fix 修不了」的循环。
- hook 注册器新增 `force_rewrite` 参数：默认 `False` 保留向后兼容的幂等行为；`doctor --fix` 显式传入 `True` 以覆盖匹配但内容过期的 hook。同一事件下无关的用户自定义 hook 不受影响。

### 变更

- `doctor --fix` 输出从「Could not register」改为更准确的「already up to date」（当 hook 完全匹配当前规范时）。

### 发布证据

- 完整 pytest：933/933 通过（新增 3 个 force_rewrite 测试，覆盖：过期升级、无操作时不写盘、共存 hook 不被误删）。
- Dogfooding 自验证：本机一个过期的 PreCompact hook（旧 `.py` 脚本路径）已被 `doctor --fix` 自动升级为 `-m` 形式。

## [3.30.0] - 2026-05-27

跨会话 / 跨工具续接 + 完整的崩溃恢复机制上线。

### 新增
- **定时心跳快照**：默认每 5 分钟保存一次会话状态（可通过 `ENGRAM_HEARTBEAT_INTERVAL` 环境变量调整），减少长会话崩溃时的数据丢失。
- **`get_resume_brief` MCP 工具**（Tier-1 核心）：返回身份卡 + 项目快照 + 每日日志 + 近期 lesson/decision 的合并简报，默认 1500-token 预算。
- **`get_daily_log` MCP 工具**（Tier-1 核心）：读取某个项目的人类可读逐日时间线。
- **每日日志层**：`~/.engram/projects/<hash>/daily/YYYY-MM-DD.md`，用 event_type 区分 session/lesson/decision/compact/checkpoint。
- **PreCompact hook**：在 Claude Code 压缩对话前触发，触发阈值低于 Stop hook（5 vs 10 轮），防止长会话被压缩时丢失状态。
- **PostCompact hook（`auto_absorb_compact.py`）**：在 Claude Code 压缩对话后触发，从压缩后的 transcript 中提取摘要写入每日日志（event_type=`compact`），并尽力调用 `extract_session_insights` 自动提取 staging 知识。超过 3000 字符的摘要会被自动截断。
- **SessionStart hook**：在新会话开始时，通过 `hookSpecificOutput.additionalContext` 协议将简报注入首轮系统提示，让用户零操作即获得续接上下文。
- **审计日志默认开启**：在启动时检测异常退出。
- **Doctor 4-hook 检查**：完整覆盖 Stop / PreCompact / SessionStart / PostCompact，`engram doctor --fix` 可自动注册缺失项。
- **Doctor 云同步目录检测**：识别 ENGRAM_DIR 是否位于 iCloud / Dropbox / OneDrive / Google Drive / NFS / SMB 挂载点并发出 WARN（这些目录中的并发写入可能导致锁文件或 JSONL 不一致）。
- **MCP 工具总数**：从 v3.29.4 的 61 增加到 65（Tier-1：16 / Tier-2：49）。

### 变更
- 通用 hook 注册器被抽取为内部基础设施，被全部四个 Claude Code hook 复用。
- doctor 的 hook 检查引入严格匹配模式，区分「任一标记命中即可」与「所有标记必须命中」。
- README / README.zh-CN 同步工具数量与 Tier-1 表格，并新增远程部署章节。
- 发布前密钥检查现已正式成为发布流程中有文档记录的一步。

### 修复
- 优化了 `save_agent_context` 跨进程合并中的 nonce 比较，避免因磁盘上 nonce 为空而导致的错误合并。
- 优化了 `_quote_for_shell` 的跨 shell 兼容性：无 shell 敏感字符时不加引号，正确处理含空格路径的引号，兼容 cmd.exe 和 PowerShell。
- 空 `project_folder` 的每日日志路径计算现在与 project_id 哈希一致。
- 在最终自动保存路径周围加锁，避免与心跳线程的边界竞态。
- 心跳函数的文档现在与其返回值语义一致。
- 多处文案 / 措辞改进（续接简报措辞更中性，对比类文档更可证伪）。

### 发布证据
- 完整 pytest：930/930 通过。

## [3.29.4] - 2026-05-27

一个由跨工具 / 跨会话审计驱动的优化版本。所有多轮回归均通过。

### 新增
- **`doctor` MCP 工具**：用户排障入口，覆盖 8 项检查（identity_completeness、health_score、stale_knowledge、near_duplicates、decision_conflicts、knowledge_volume、quick_context_freshness、identity_provenance）。默认包含在核心层。
- **字段级溯源**：profile 现在为每个字段记录 `_provenance: {by, at}`，连同 `_last_updated_by`，更便于跨工具追踪「是谁改了我的偏好」。
- **`update_identity` MCP 新增 `source_tool` 参数**：传入后会在 profile 中记录一条来源条目。
- **类型感知的过期衰减**：`STALE_DECAY_MULTIPLIERS` 按 domain 调整过期阈值（`user_preference=3.0`、`architecture=2.0`、`workflow=1.0`、`debug=0.5`），避免长期偏好被误判为过期。
- **跨工具使用指南**：新增 `docs/cross-tool-guide.md`，涵盖配置、自动恢复、多工具共存以及 doctor 排障流程。
- **回归守卫测试**：`tests/test_optimizations_v3294.py` 锁定 6 项关键回归（description 重写、三工具共存、decision 无自引用、lesson 无自引用、doctor 核心）。

### 变更
- **三级 Lesson/Decision 去重**（duplicate / related / pass）：
  - `SIMILARITY_DUPLICATE_THRESHOLD` 从 0.85 提高到 0.95，避免「补充案例」被误判为重复。
  - 引入 `_SUPPLEMENT_MARKERS`（包含 `补充/案例/反例/边界/edge case` 等），即使相似度高也允许走 related 路径。
  - 0.55 ≤ sim < 0.95 的条目被双向写入 `related_ids`，并附带 `_dedup_note`。
- **Decision ID 生成**：ID 种子现在包含 `choice`，避免「同一问题、不同选项」产生相同 ID。
- **字段级 description 合并**：多个工具写入的标记可以共存；重写某个已有标记不会丢失其他工具的写入。
- **`get_lessons` / `get_decisions` 默认不再更新 access_count**：身份卡等读取路径不再产生副作用。
- **`related_ids` 自引用守卫**：lesson / decision 关联时跳过自身 ID，避免 `related_ids: [self]`。

### 修复
- `doctor` 调用了不存在的 `knowledge_overview` 方法 → 改为 `get_knowledge_overview()`。
- 重写某个已有的 description 标记会覆盖其他工具的标记。
- 一条 decision 的 `related_ids` 对同一问题不同 choice 显示了自引用。

### 发布证据
- 所有多轮回归测试均通过。

## [3.29.0] - 2026-05-24

AI 指令自动注入、hooks 适配器、激活漏斗、对比文档。

### 新增
- **AI 指令自动注入**：`engram setup` 现在将指令片段注入每个工具的原生配置文件（`CLAUDE.md`、`.cursorrules`/`.mdc`、`AGENTS.md`），让 AI 主动调用 Engram，而不仅依赖 MCP server 指令
- **Claude Code Stop hook 自动注册**：`engram setup` 在 Claude Code 的 `settings.json` 中注册会话自动保存 hook；`engram doctor --fix` 可修复
- **Stop hook 增强**：`auto_save_on_stop.py` 现在对有实质内容的会话（10+ 条消息）调用 `wrap_up_session()`，将 lesson/decision/playbook 草稿提取到 staging
- `engram doctor` 检查 Claude Code Stop hook 的注册状态
- `_inject_instruction_snippet()` / `_remove_instruction_snippet()` —— 基于标记的幂等更新的程序化注入
- Setup 问题的 Issue 模板（`.github/ISSUE_TEMPLATE/setup_problem.md`），用于激活漏斗反馈
- Issue 模板选择器配置（`.github/ISSUE_TEMPLATE/config.yml`），带 Discussions 与 Security 链接
- `setup_report.jsonl` —— 本地 setup 结果跟踪，用于激活漏斗分析
- 20 个新测试：指令注入（10）、setup 报告（6）、hook 注册（4）
- `docs/comparison.md` 以 3 类竞品定位重写

### 变更
- MCP server 指令重写：结构化的「WHEN TO CALL」格式，含 5 个明确触发点
- README 标语更新：「AI can suggest memories. You decide what becomes true.」
- `docs/comparison.md` 以 3 类结构重写（agent memory / project memory / personal identity）
- `engram doctor` 现在调用 `_configure_utf8_stdio()` 以修复 Windows GBK 控制台的中文显示
- Doctor 段落标题改用 ASCII 安全字符，替代制表符 Unicode

## [3.28.1] - 2026-05-24

自动项目快照与会话中检查点。

### 新增
- MCP server 退出时自动生成项目快照 —— 收集版本、模块数、测试数、MCP 工具数
- `_collect_project_info()` 辅助函数，用于基于文件系统的项目指标
- Stop Hook（`auto_save_on_stop.py`）同时更新项目快照

### 修复
- 测试隔离：`isolated_engram` fixture 现在重置 `_session`，防止 atexit 数据泄漏到真实的 `~/.engram/`

## [3.28.0] - 2026-05-23

会话自动跟踪与执行计划修复。

### 新增
- 通过 `_SessionTracker` 实现 MCP server 会话自动跟踪 —— 记录会话期间的所有工具调用
- `atexit` 自动保存：在 MCP server 关闭时持久化会话上下文（工具列表、调用次数、时长）
- Claude Code Stop Hook 脚本（`scripts/auto_save_on_stop.py`）—— 对话结束时保存会话元数据
- 三层会话保护：AI 手动保存（高质量）→ MCP atexit（中）→ Stop Hook（基础）

### 修复
- `prepare_playbook_execution` 现在在核心层自动保存执行计划（此前仅在 MCP 层保存，导致通过 Python API 调用时数据丢失）
- 移除 MCP 层中冗余的 `save_execution_plan` 调用（现由核心层处理）

## [3.27.1] - 2026-05-23

### 修复
- 遥测的 opt-in 现在是正常 setup 向导流程的一部分，不再隐藏在 `--advanced` 之后
- 身份卡内容质量：限制 domains、过滤配置指令、清理 XML 残留

## [3.27.0] - 2026-05-23

执行跟踪、统计 i18n、steps 格式兼容。

### 新增
- Playbook 执行跟踪：`prepare_playbook_execution` → `update_execution_step` → `get_execution_status`
- 带 `t(zh, en)` 的 i18n 模块，用于统计中的双语输出

### 修复
- 在 playbook 参数提取、合并和执行中处理字符串格式的 steps

## [3.26.0] - 2026-05-23

Playbook 生命周期、双语 UX、知识智能。

### 新增
- Playbook 自动提取改进
- 工具登记表作为 Tier-1 知识类型

## [3.25.0] - 2026-05-23

### 变更
- Playbook 自动提取 P0 改进
- 提升 MCP Registry server.json 版本

## [3.24.0] - 2026-05-23

第 2 阶段远程遥测，带 Cloudflare Worker 仪表板。

### 新增
- 通过 Cloudflare Worker + D1 实现的 opt-in 远程匿名使用统计
- 可视化遥测仪表板（中文，密码保护），含 PyPI 下载统计
- 周期性遥测刷新（每 10 次工具调用），防止退出时数据丢失
- `atexit` 处理器作为 MCP server 关闭时的兜底刷新
- `ToolCallTracker.flush()` 上的 `force` 参数，可绕过每日速率限制
- 远程同意：`engram telemetry remote on/off/status` CLI 命令
- 18 个新遥测测试（远程配置、发送器、payload 字段）

### 变更
- `wrap_up_session` 现在强制刷新遥测（此前若当天已刷新则跳过）
- 遥测 payload 包含 `os_platform`、`python_version`、`tools_tier` 字段

## [3.23.0] - 2026-05-23

新知识类型：**Playbook** —— 以独立文件存储的结构化操作流程，便于未来分享。

### 新增
- Playbook 知识类型：带触发关键词的多步骤操作流程
- 独立文件存储（`~/.engram/playbooks/<id>.json`），带轻量索引
- 基于触发词的检索：用关键词锚点实现即时召回（例如搜索「发布 registry」即可找到发布流程）
- MCP 工具：`add_playbook`（Tier-1）、`get_playbooks`、`get_playbook`
- `search_knowledge` 扩展支持 `scope="playbooks"`
- 触发词精确匹配评分加成（每次命中权重 5.0），实现高精度检索
- `export_all` / `import_all` 中的 Playbook 支持，用于备份与迁移
- `update_knowledge` / `archive_knowledge` / `_find_item_by_id` 中的 Playbook 支持
- `evaluate_tiers` 中的 Playbook 层级提升
- 覆盖完整 playbook 生命周期的 15 个新测试

### 变更
- `FIELD_WEIGHTS` 扩展，新增 `triggers`（4.0）和 `description`（2.0）
- `_score_item` 现在处理列表类型字段（向后兼容）
- `_TERM_ALIASES` 扩充了 playbook/publish 词汇

## [3.22.2] - 2026-05-23

搜索发现与转化优化版本。

### 变更
- README 以痛点语言重写，面向 GEO/SEO/AIEO 搜索发现
- 新增按客户端划分的配置块（Claude Code、Cursor、Codex、Claude Desktop、Windsurf）
- FAQ 以搜索优化的问答重写，便于 AI 引用
- 中文 README 与英文版同步
- pyproject.toml 的 description 与 keywords 为搜索发现更新
- MCP Registry 描述更新为「persistent memory」表述

## [3.22.1] - 2026-05-23

MCP Registry 分发版本。

### 新增
- 官方 MCP Registry `server.json`（`.mcp/server.json`）
- README 中的 `mcp-name` 标签，用于 PyPI 所有权验证
- CODE_OF_CONDUCT.md（Contributor Covenant v2.0）

### 变更
- Smithery 列表已发布并设为公开

## [3.22.0] - 2026-05-23

Doctor 升级与上手打磨版本。

### 新增
- **`engram doctor` 功能性检查**：配置健康扫描后，doctor 现在会验证核心库导入、Engram 初始化、身份 profile、quick_context.md 以及 MCP 工具注册
- **Setup 完成后的验证指引**：setup 结束后给出清晰的下一步说明

### 变更
- CI 工作流启用 Node.js 24（`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`），消除 GitHub 弃用警告
- 清理共享指令：删除 30 行过期的版本历史，将 Tier-1 工具列表更新为 13 个工具

### 修复
- 修正 CHANGELOG v3.21.0 的工具数（原为 43→46，现为 45→48）

## [3.21.0] - 2026-05-23

Agent 上下文自动保存版本 —— 恢复丢失的 AI 对话。

### 新增
- **Agent 上下文自动保存**：办公软件式的 AI 会话上下文自动保存。在关键检查点（任务开始、里程碑、方向变更）静默记录工作状态；在工具重启或会话断开后可按需恢复
- **`save_agent_context` MCP 工具**：按工具保存或追加上下文检查点，带 session ID 以支持多检查点会话
- **`get_recent_context` MCP 工具**：在上下文丢失（工具重启、会话断开）后检索最近的会话上下文
- **`list_agent_sessions` MCP 工具**：浏览所有工具中可用的会话记录（仅元数据）
- **`ContextStoreMixin`**（contexts.py）：新 mixin，在 `~/.engram/contexts/{tool}/` 中按工具进行会话文件存储
- 存储：仅追加的 markdown 文件，永不自动过期或自动删除
- 14 个新测试，覆盖上下文保存、追加、恢复、列举和工具隔离
- 全部 3 个上下文工具加入 Tier-1（始终可用）

### 变更
- MCP Tier-1 工具增加：10 → 13（新增 save_agent_context、get_recent_context、list_agent_sessions）
- MCP 工具数增加：45 → 48
- 目录结构：init 时在 `~/.engram/` 下新增 `contexts/`

## [3.20.0] - 2026-05-23

知识健康评分与智能去重版本。

### 新增
- **知识健康评分**：`get_knowledge_overview(section="health")` 现在返回 0–100 的综合 `health_score`，含四维分解：新鲜度（30 天内已复核的占比）、质量（verified 与 staging 之比）、覆盖度（通过香农熵衡量的 domain 多样性）、整洁度（无重复/无归档候选）
- **`suggest_merges` MCP 工具**：对全知识库扫描相似度高于阈值（默认 0.45）的近重复条目。返回可执行的合并命令 —— 每条建议包含 primary/secondary ID、摘要、相似度分数以及一条可直接调用的 `merge_knowledge()` 命令
- 健康评分各维度与 suggest_merges 功能的测试

### 变更
- README 更新，描述健康评分维度与 `suggest_merges` 工具
- MCP 工具数增加：19 读 + 17 写 + 1 web + 4 导入/导出 + 2 工作流 = 43 个工具

## [3.19.0] - 2026-05-23

冷启动优化版本 —— 解决「装了却从未使用」的缺口。

### 新增
- **环境自动探测**：`engram setup` 现在自动检测姓名、邮箱（来自 git config）、技术栈（来自项目文件）、语言偏好（来自提交历史）和提交风格
- **种子知识模板**：Setup 根据检测到的技术栈注入最佳实践 lesson（Python、TypeScript、Go、Rust、Java + 通用），标记为 `staging` 层
- **引导式空状态响应**：在空 Engram 上调用 `get_user_context` 现在返回 5 步 AI 上手指南，而非裸的「no context」消息
- **在 setup 向导结束时自动刷新 `quick_context.md`** —— 所有 AI 工具可立即读取
- **分发监控脚本**（`scripts/metrics.py`）：跟踪 GitHub 流量、PyPI 下载、引荐来源以及本地使用信号
- 4 个新测试，覆盖冷启动函数（探测、种子模板、去重、空目录）

### 变更
- **README 中支持工具表扩展到 13 项**（原为 6 项）：4 个已验证 + 7 个预期可用 + OpenClaw + ChatGPT 兜底
- **「Status」列改名为「Confidence」**，表述更清晰
- Setup 菜单选项现在根据探测到的环境信号预先排序

## [3.18.0] - 2026-05-23

仓库改名、安全加固与 doctor 升级版本。

### 变更
- **GitHub 仓库改名** `Patdolitse/engram` → `Patdolitse/piia-engram`（避免与 Gentleman-Programming/engram 3.7k stars 撞名）
- **模块改名完成**，覆盖所有文件：`engram_core` → `piia_engram`（保留带 `DeprecationWarning` 的向后兼容垫片）
- **`engram doctor` 扩展到 11 个 AI 工具**（原为 6 个）：Claude Code、Cursor、Claude Desktop、Codex + 7 个社区支持（Windsurf、Copilot、Cline、Roo Code、Amazon Q、Augment、Zed）
- **Doctor 输出现在显示 verified 与 community 层级** —— 清晰标注团队已测试 vs 未测试的工具
- **社交预览图更新**为 piia-engram 品牌

### 安全
- **从 git 中移除 20 个被跟踪的结果/数据文件**（基准测试输出、含 LLM payload 的评估日志）
- **清除 4 个硬编码的个人路径**（Windows 用户名），来自报告与文档
- **.gitignore 加固** —— 新增 `.env.*`、`*.pem`、`*.key`、`credentials*`、`secrets*`，以及更广的评估结果模式
- **CI 工作流收紧** —— 在 ci.yml 和 publish.yml 上显式设置 `permissions: contents: read`

### 测试
- **674 通过**，0 失败
- 改名后验证：10/10 项检查 PASS（旧导入、URL、包元数据、CLI 入口点、doctor 覆盖、向后兼容）

## [3.17.0] - 2026-05-23

质量与可靠性版本：657 个测试，96% 覆盖率（所有模块 ≥90%），跨平台 CI 修复，以及达成 43/43 PASS 的 Round 10 检索质量基准。

### 新增
- **冷启动 setup 精简** —— 简化首次运行体验，带引导式 setup 流程
- **Round 10 检索/注入质量基准** —— 7 维、43 个用例的测试套件；在外部 LLM 评判下全部 43 个 PASS

### 修复
- **CI 稳定性** —— 安全的波浪号展开（不在路径字面量的 `~` 上用 `os.path.expanduser`）、测试鉴权加固、作业矩阵从 12→6 以加快反馈
- **跨平台路径解析** —— `_sanitize_project()` 使用 `PureWindowsPath`，使 Windows 路径在所有平台都能正确解析

### 测试
- **657 通过**（从 v3.16.0 的 490 提升；新增 +167）
- 总覆盖率：**83% → 96%**（+13pp）；所有模块 ≥90%
- 关键模块覆盖率：storage 100%、core 95%、reconcile 98%、mcp_server 99%、setup_wizard 93%、reports_identity 100%、stats 100%

### 基准
- Round 10：检索质量 7 维 43/43 PASS（相关性、完整性、噪声、格式、延迟、边界情况、注入安全）

## [3.16.0] - 2026-05-22

代码质量版本：拆分了最后一个单体模块，将 mcp_server 覆盖率提升到生产级，并进行了第三方里程碑评估。

### 变更
- **`reports.py` 拆分为 5 个模块**（1103 行 → 每文件最多 520 行）：
  - `reports.py`（22 行）—— 组合 4 个子 mixin 的薄壳
  - `reports_rarity.py`（85 行）—— `RarityMixin`：质量分类 + `RARITY_TIERS`
  - `reports_review.py`（520 行）—— `ReviewMixin`：HTML 复核页、promote/archive
  - `reports_identity.py`（97 行）—— `IdentityCardMixin`：Markdown 身份卡导出
  - `reports_analytics.py`（310 行）—— `AnalyticsMixin`：健康报告、过期检测、digest、统计
- 公共 API 不变 —— `from piia_engram.reports import ReportsMixin` 仍然可用
- `architecture.md` 更新到 v3.16.0，含新模块图与两级 mixin 图
- README 的「By the numbers」更新到 v3.16.0 统计（490 个测试，83% 覆盖率）
- CONTRIBUTING 测试基线更新：490+ 个测试，83%+ 覆盖率

### 测试
- **490 通过**（从 v3.15.1 的 437 提升；新增 +53）
- 新增 `tests/test_mcp_coverage.py`（53 个测试）—— 覆盖写工具、搜索、复核/合并、身份更新、导入/导出、工作流快捷方式以及全部 7 个 MCP 资源
- `mcp_server.py` 覆盖率：**58% → 86%**（+28pp）
- 总覆盖率：**78% → 83%**（+5pp）

### 评估
- 外部 3-pass 里程碑评估：架构 8.0（+0.5）、安全 8.0（+0.5）、总体 7.53
- v3.14.3 的 5/5 建议已验证修复
- 关键反馈：architecture.md 和 CONTRIBUTING.md 滞后（现已修复）

## [3.15.1] - 2026-05-22

### 修复
- **GBK 控制台安全**：setup 向导中的身份卡预览现在使用 `_safe_print()`，以避免在 Windows 中文控制台上出现 `UnicodeEncodeError`（剥离不支持的 emoji，保留 CJK 文本）

### 改进
- **README**：新增 PyPI 下载徽章、「30 秒」快速开始表述、setup 第 5-6 步（隐私 + 身份卡预览）、将「By the numbers」更新到 v3.15.0 统计（437 个测试）、新增 CLI 命令参考章节
- **README.zh-CN.md**：同步所有英文 README 改进
- **CONTRIBUTING 基线**：394+ → 437+ 个测试

## [3.15.0] - 2026-05-22

聚焦隐私的功能版本：opt-in 匿名使用统计、reconcile 授权关卡、setup 向导隐私步骤。通过跨 AI 咨询设计（综合 4 项独立 AI 评估）。

### 新增
- **匿名使用统计（第 1 阶段：仅本地日志）** —— `telemetry.py` 模块
  - 默认关闭；在 `engram setup` 第 5 步或通过 `engram telemetry on` 选择开启
  - 仅收集 4 个字段：工具调用分布（成功/错误计数）、知识条目总数、engram 版本、每日匿名 ID
  - 每日 ID 通过 `HMAC(local_uuid, date)` 生成 —— 无法跨天关联
  - Payload 校验器拒绝 >200 字符或含自然语言模式的字符串（不可能泄露内容）
  - 所有数据存储在本地 `~/.engram/telemetry.log`（JSONL，人类可读）
  - **无网络请求** —— 第 2 阶段以 30 天 + 5 名用户分享日志为前置条件
  - CLI：`engram telemetry status|preview|on|off`
  - 环境变量覆盖：`ENGRAM_TELEMETRY=0|1`
- **Reconcile 授权关卡** —— `reconcile.py`
  - `reconcile_memories()` 和 `reconcile_ai_configs()` 现在需要显式授权
  - 通过 `ENGRAM_RECONCILE` 环境变量或 `telemetry_config.json` 偏好控制
  - 默认：已授权（对现有用户向后兼容）
  - 新用户在 setup 期间显式选择
- **Setup 向导第 5 步：隐私偏好**
  - [1] 跨工具记忆同步授权（默认：是）
  - [2] 匿名使用统计（默认：**否**）
  - 数字选择 UI（无自由文本输入）
- **ToolCallTracker 接入 MCP server** —— 10 个 Tier-1 工具被插桩成功/错误跟踪；在 `wrap_up_session` 期间自动刷新
- 内部遥测规划笔记（分阶段推出 + 决策关卡标准）

### 变更
- `README.md` / `README.zh-CN.md`：更新「0 网络调用」说法以反映 opt-in 统计；FAQ 重写
- `SECURITY.md`：从「无遥测」更新为描述 opt-in 匿名统计，含 preview/off 说明
- `docs/comparison.md`：将「无可关闭的遥测」说法更正为描述 opt-in 模型

### 测试
- **424 通过**（从 v3.14.4 的 394 提升；新增 +30）
- 新增 `tests/test_telemetry.py`（30 个测试）：配置持久化、环境覆盖、每日 ID 属性、payload 校验（长度/语言/嵌套）、build_payload 关卡、本地日志追加、preview、ToolCallTracker 生命周期、opt-out 安全
- CONTRIBUTING 基线提升：394+ → **424+ 个测试**

## [3.14.4] - 2026-05-22

由 v3.14.3 里程碑评估驱动的补丁。处理了两个高严重度发现；完整回归上下文已内部记录。

### 安全
- **`crypto.py`：`DecryptionError` + `strict=True` 模式**。默认的 `decrypt()` 在失败时仍返回原始密文（向后兼容的警告 + 透传），但新调用方现在可选择 `decrypt(value, strict=True)` / `decrypt_fields(..., strict=True)` 以改为抛出 `DecryptionError`。使用 `raise from None` 以避免泄露关于哪个阶段失败的时序预言机信息（b64 / 密钥派生 / AEAD tag）。
- 默认行为为任何可能已依赖它的调用方保留向后兼容，但 docstring 现在明确警告：「callers that don't validate the prefix after this call may treat ciphertext as plaintext — prefer strict=True in new code.」

### 修复
- **README MCP 工具数不一致**。README 的「By the numbers」声称 45 个工具，而别处说 43 个；实际数量为 **43**（`grep -c '^@mcp.tool' src/piia_engram/mcp_server.py`）。所有文档现已统一为 43：
  - `README.md` 和 `README.zh-CN.md` 的量化章节 + 对比表
  - `docs/comparison.md`
  - `docs/architecture.md`（3 处引用）
  - 内部覆盖率 + 评估笔记（带明确的勘误说明）

### 测试
- **394 通过**（从 v3.14.2 的 386 提升；v3.14.3 仅文档）
- `tests/test_crypto.py` 中新增 `TestDecryptionStrict` 类（8 个测试）：错误密钥抛出、坏 payload 抛出、截断 payload 抛出、strict 模式下无前缀透传、正常路径往返、默认模式不变、`__cause__` 为 None（无时序泄露）、`decrypt_fields(strict=True)` 抛出且不改动输入 dict
- CONTRIBUTING 基线提升：386+ → **394+ 个测试**

### 文档
- v3.13.2 → v3.14.3 的里程碑评估收口（外部多轮审查）
  - 架构评分：5.4 → 7.50（+2.10，最大变动）
  - 总体：6.9 → 7.90（+1.00）
  - 自评校准偏差从 +1.7（安全盲点）收窄到 −0.5（现在略偏保守）
  - v3.13.2 的 15/21 个问题标记为 `fixed`，5 个 `partial`，1 个 `unverified`，0 个 `regression`
  - 为 v3.15.0 提取的路线图项：拆分 reports.py（1103 行）、明确 Mixin 依赖、新增 SSE 集成测试、mock LLM 提取

## [3.14.3] - 2026-05-22

### 文档
- 新增 `docs/architecture.md` —— 30 秒心智模型图、完整模块图（v3.14.1 重构后）、三条规范数据流（冷启动 / 捕获 / 复核）、存储布局、MCP 面、约定、「在哪里添加东西」矩阵
- 新增 `docs/comparison.md` —— 与 Letta、Mem0、Cline memories、Claude Code memory 的事实性并排对比；明确的「何时该选别人」章节；身份层 vs 记忆层的架构表述
- README 升级：对比表扩展到 5 个竞品，维度更清晰（用途、本地性、加密、知识层级、冲突检测）；新增「By the numbers」章节，含 v3.14.2 量化说法（45 个 MCP 工具、386 个测试、78% 覆盖率、PBKDF2 600k、< 100ms 冷启动、核心 0 网络调用）；中英双语
- README FAQ：解释 `piia-engram` 这个 PyPI 名称与「Engram」产品品牌的关系（英文 + 中文）

### 测试
- 不变 —— 386 通过（本版本无代码改动）

## [3.14.2] - 2026-05-22

### 测试
- **386 通过**（从 v3.14.1 的 329 提升，新增 +57）
- 新增 `tests/test_mcp_tools.py`（37 个测试）—— 直接覆盖 MCP 工具封装：身份读取、知识读写、搜索、上下文、错误捕获、Tier-1 过滤、路径校验
- 新增 `tests/test_review_page_xss.py`（10 个测试）—— 验证 `_esc` 转义可防止复核 HTML 页中的 HTML / 属性注入（lesson 摘要、decision 标题、domain 标签、profile 字段、source_tool、与号、CJK 透传）
- 扩展 `tests/test_crypto.py`（+10 个测试，现为 19）—— v1↔v2 混合字段解密、v1→v2 重加密升级、Unicode（emoji/CJK/RTL/组合字符）、坏 base64 / 截断 payload / 未知前缀透传、非字符串字段跳过、迭代次数固定、默认前缀为 v2 契约

### 安全
- **路径校验**：`mcp_server.py` 中新增 `_validate_path` 辅助函数，拒绝用户提供路径中的 NUL 字节。应用于 `import_engram`、`export_engram`、`save_project_snapshot`。Engram 仍是本地优先（不是沙箱），但空字节处理现在符合 OWASP 对跨越信任边界路径的指引。

### 文档
- 发布首个测试覆盖率基线，并在内部记录剩余缺口
- 新增 `.coveragerc` —— 固定源根与排除规则，使未来运行可复现
- CONTRIBUTING 基线提升：329+ 个测试 → 386+ 个测试，要求 78%+ 覆盖率

## [3.14.1] - 2026-05-22

### 重构
- **`core.py` 拆分**：4277 → 1083 行（-74.7%），通过 mixin 模式抽取为 7 个模块。公共 API 不变 —— 所有来自 `piia_engram.core` 的导入通过 re-export 继续可用。
  - `storage.py`（224）—— 常量 + I/O 原语（`_read_json`、`_write_json`、`_engram_root` 等）
  - `retrieval.py`（639）—— `RetrievalMixin`：搜索、评分、分词、批量操作、冲突检测
  - `context.py`（688）—— `ContextMixin`：`generate_context`、摄取 + 独立的 `extract_knowledge` / `ingest_extraction`
  - `reconcile.py`（425）—— `ReconcileMixin`：外部 AI 记忆 + 配置文件同步
  - `reports.py`（1103）—— `ReportsMixin`：复核 HTML、身份卡、健康、统计、知识 digest
  - `compat.py`（318）—— OpenClaw / OCA 迁移函数
  - `core.py`（1083）—— `Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin)` 门面

### 安全
- **PBKDF2 迭代次数：100,000 → 600,000**（OWASP 2023+ 推荐下限）。新加密使用 `enc:v2:` 前缀。
- **向后兼容**：`enc:v1:` 密文（遗留 100k 迭代）继续可解密。旧数据在该字段下次写入时重加密为 v2。

### 修复
- **Schema 版本比较**：`_migrate_v1_to_v2` 使用了字典序字符串比较（`"10.0" < "2.0"`）。现在通过 `_parse_schema_version` 解析为元组。

### 变更
- **`print(file=sys.stderr)` → `logging`**，覆盖所有 piia_engram 模块（audit、compat、context、crypto、mcp_server、setup_wizard、stats、storage）。每个模块获得 `logger = logging.getLogger(__name__)`。库的输出现在尊重宿主应用的 logging 配置。

### 测试
- **329 通过**（从 v3.14.0 的 328 提升）
- 新增：`test_v1_ciphertext_still_decrypts` —— 验证 PBKDF2 升级后对遗留 v1 密文的前向解密

## [3.14.0] - 2026-05-22

### 破坏性变更
- **加密快速失败**：当设置了 `ENGRAM_SECRET` 但缺少 `cryptography` 包时，`EncryptionEngine` 现在抛出 `RuntimeError`。此前它会静默禁用加密，存在明文存储风险。

### 安全
- **时序攻击修复**：SSE token 比较从 `==` 改为 `secrets.compare_digest`
- **SECURITY.md 更正**：「Fernet」→「AES-256-GCM」以匹配实际实现
- **SSE 加固**：`0.0.0.0` 绑定会发出 HTTPS 警告；新增 `ENGRAM_CORS_ORIGINS` 环境变量用于跨域限制
- **sys 导入修复**：`core.py` 缺少顶层 `import sys` —— 错误处理器本会抛出 `NameError` 而非记录日志

### 修复
- `_apply_tool_tier` docstring 更正（核心是默认值，而非全部）
- 移除 mcp_server.py 启动同步块中冗余的 `import sys as _sys`
- README：「100% local」→「local-first」（如实说明 `read_web_content` 的网络路径）
- README：「automatically」→「one tool call away」（知识继承需要显式调用）
- README：过期知识天数 90 → 30（匹配 `STALE_KNOWLEDGE_DAYS` 常量）
- README FAQ：安装路径统一为 `pip install piia-engram && engram setup`
- README：新增带 JSON 片段的 `ENGRAM_TOOLS=all` 配置示例
- README：在 SSE 安全说明中新增 `ENGRAM_CORS_ORIGINS`
- 所有修复同时应用于英文和中文 README

### 测试
- 328 通过（从 v3.13.2 的 327 提升）
- 新增：`test_secret_without_crypto_raises` —— 验证缺少 cryptography 时的快速失败

### 文档
- v3.13.2 里程碑评估收口（内部记录）

## [3.13.2] - 2026-05-22

### 测试
- **327 通过**（从 v3.13.1 的 281 提升）—— 46 个新测试，覆盖关键算法缺口
- 新增：7 个 `_score_item` 测试（字段权重、访问加成、多词覆盖、CJK 查询）
- 新增：4 个 `search_knowledge` 测试（排序、CJK 搜索、别名展开、阈值过滤）
- 新增：4 个 `_detect_decision_conflicts` 测试（相同/不同 domain、重叠 domain）
- 新增：4 个 `_detect_lesson_conflicts` 测试（否定/肯定标记、CJK、domain 分离）
- 新增：7 个 `generate_context` 测试（空 profile、token 预算、冲突章节、章节包含）
- 新增：8 个 `ingest_notes` 测试（decision/lesson 触发、短行跳过、去重、CJK 触发）
- 新增：4 个 `_infer_domain` 测试（单/多匹配、兜底行为）
- 新增：4 个 `_bigram_similarity` 测试（完全相同、空、部分、完全不同）
- 新增：2 个 `evaluate_tiers` + 1 个驱逐测试（staging 优先驱逐策略）

## [3.13.1] - 2026-05-22

### 修复
- **CJK 行分类**：中文行（例如「我是全栈开发者」）在规则文件导入时被错误跳过，因为最小长度阈值（8 字符）未考虑 CJK 字符密度。现在对 CJK 文本使用 4 字符阈值。
- **规则目录通配**：`reconcile_ai_configs` 现在能正确从目录式配置（例如 `~/.cursor/rules/*.mdc`）导入规则文件，而非静默跳过。
- **过期知识显示**：剩余的 3 处硬编码「30 天」字符串现在一致地使用 `STALE_KNOWLEDGE_DAYS` 常量。

### 测试
- 281 通过（从 v3.13.0 的 258 提升）
- 新增：22 个参数化 `_classify_line` 测试，覆盖 CJK、用户身份、项目规则、跳过和歧义情况
- 新增：2 个 `_scan_rule_files` 测试（项目检测 + 微小文件跳过）
- 新增：`reconcile_ai_configs` 目录通配测试

## [3.13.0] - 2026-05-22

### 破坏性变更
- **默认工具集改为 Tier-1 Core（10 个工具）**。此前默认加载全部 43 个工具。在你的 MCP 配置 `env` 中设置 `ENGRAM_TOOLS=all` 以恢复完整集。若你的配置未指定 `ENGRAM_TOOLS`，`engram doctor` 会显示一条信息提示。

### 变更
- **Tier-1 工具集修订**：新增 `wrap_up_session`（会话生命周期）和 `update_identity`（profile 更新）；移除 `extract_session_insights` 和 `export_engram`（移至 Tier-2）
- **快速开始简化**：`pip install piia-engram && engram setup` 即完整流程；手动 MCP JSON 配置移入可折叠章节
- README 工具表重新组织：Tier-1 作为主表，Tier-2 放入可折叠的 `<details>` 章节

### 改进
- 当配置缺少 `ENGRAM_TOOLS` 设置时，`engram doctor` 显示信息提示
- 抽取 `MAX_KNOWLEDGE_ENTRIES` 常量（此前在 11 处硬编码为 `200`）

## [3.12.3] - 2026-05-22

### 修复
- **JSON 损坏日志**：`_read_json()` 现在在解析失败时向 stderr 发出警告，而非静默返回空数据
- 最后 3 个静默异常块（stats.py、crypto.py）现在记录到 stderr —— 所有源文件**零静默异常**

### 改进
- 抽取 `SEARCH_RELEVANCE_THRESHOLD`、`STALE_KNOWLEDGE_DAYS` 和 `MAX_KNOWLEDGE_ENTRIES` 为模块常量（此前共在 17 处硬编码）
- CI 工作流：新增 pip 缓存以加快运行
- README 工具表现在列出全部 43 个工具（此前缺少 `apply_review` 和 `request_outline_review`）
- 用正确的导入替换 `__import__('sys')` 技巧

### 测试
- 258 通过（从 v3.12.2 的 242 提升）
- 新增：12 个 staging/review/rarity 工作流测试（classify_rarity、evaluate_tiers、apply_review、promote_knowledge）
- 新增：3 个 export_all/import_all 错误处理测试

## [3.12.2] - 2026-05-22

### 新增
- **搜索别名展开**：16 个新 CJK/英文别名对（js→javascript、db→数据库、部署→deploy、前端→frontend 等）
- **CJK 三元组别名查找**：3 字符中文词（例如「数据库」）现在能在搜索时正确展开为英文别名

### 改进
- 移除冗余的 `test.yml` 工作流 —— `ci.yml` 已覆盖 3 OS × 4 Python 版本

### 测试
- 242 通过（从 v3.12.1 的 224 提升）
- 新增：16 个 `export_to_openclaw`、`import_from_openclaw`、`migrate_from_oca_memory`、`increment_domain_usage` 测试
- 新增：2 个别名展开测试（缩写 + 跨语言搜索）

## [3.12.1] - 2026-05-22

### 修复
- **搜索排序**：多词查询现在通过覆盖加成正确地优先匹配更多查询词的条目（D6-RANK-01 基准修复）

### 改进
- pyproject.toml 中的 SPDX license 格式（消除 setuptools 弃用警告）
- pytest `pythonpath` 配置替换所有测试文件中的 `sys.path.insert` 技巧

### 测试
- Round 10 基准：43/43（100%），从 40/43 提升

## [3.12.0] - 2026-05-22

### 改进
- 冷启动空状态指引：可执行的下一步（update_identity / engram setup），而非裸警告
- 所有静默的 `except Exception: pass` 块现在记录到 stderr 以便调试
- Python 3.13 加入 CI 测试矩阵和 PyPI classifiers

### 测试
- 212 通过（从 v3.11.2 的 193 提升）
- 新增：`test_stats.py` —— 11 个测试，覆盖 stats 模块（API mock）
- 新增：3 个 `engram doctor` 测试（健康配置、遗留名称、无效路径）
- 新增：5 个边界情况测试（token 预算、CJK 冲突、配置大小限制）

### 文档
- 双语 issue 模板（bug 报告 + 功能请求）
- 带安全检查清单的双语 PR 模板
- 将 9 个独立的 RELEASE_NOTES 文件合并到 CHANGELOG.md
- 复核工具的双语 docstring

## [3.11.2] - 2026-05-22

### 安全
- `export_identity_card()` 现在尊重 `trust_boundaries.restricted_fields`
- `get_profile` MCP 工具默认改为 `safe=True`
- `engram://identity/profile` 资源端点现在返回安全（已过滤）的 profile
- 在 `update_profile`、`update_preferences`、`update_trust_boundaries`、`update_quality_standards` 上进行字段白名单校验
- `reconcile_memories` 跳过 > 10 KB 的文件；`reconcile_ai_configs` 跳过 > 50 KB 的文件
- 每次 reconcile 运行后写入审计日志

### 测试
- 193 通过（7 个新安全测试）

## [3.11.1] - 2026-05-22

### 变更
- 为 PyPI 提升版本（3.11.0 文件名已被占用）

## [3.11.0] - 2026-05-22

### 新增
- **知识冲突检测** —— `generate_context()` 对矛盾的 decision（同 domain + 相似问题 + 不同 choice）和矛盾的 lesson（情感不对称）发出警告
- **Token 预算控制** —— `generate_context(max_tokens=N)` 优先丢弃低优先级章节；11 个章节按优先级排序
- **Staging 待办提醒** —— `wrap_up_session` 和 `generate_context()` 提示未复核的自动导入条目
- **简化的 rarity 系统** —— 3+1 层（legendary/epic/rare + staging 灰色）；截断时 staging 优先驱逐
- **自动同步** —— `reconcile_memories()` + `reconcile_ai_configs()` 从 Claude Code memory、CLAUDE.md、.cursorrules 等导入
- **交互式复核页** —— 基于浏览器的知识复核，带 domain 分组、rarity 徽章、保留/归档切换
- SECURITY.md —— 双语漏洞报告政策
- NOTICE —— Apache 2.0 归属文件

### 修复
- **P0**：截断现在优先驱逐 staging 条目（永不丢弃 verified 知识）
- **P1**：移除 staging 自动提升；提升仅通过 `evaluate_tiers()`
- **P1**：XSS —— 所有用户可控的 HTML 字段通过 `_esc()` 转义，包括 domain 分组标题
- **P1**：归档假成功 —— `apply_review` 正确检查 `result.get("error")`
- **P1**：Frontmatter 解析 —— 内容正文中的 `---` 不再切换 frontmatter 模式
- **P1**：嵌套项目路径 —— 贪婪递归的 `_decode_claude_project_name()`
- **P2**：复核页 `access_count` 污染 —— `generate_review_page()` 使用 `_update_access=False`

### 测试
- 186 通过；Round 10 基准 7 维 43/43

## [3.10.1] - 2026-05-19

### 修复
- 上下文质量热修复：lesson 分配、domain 清理、空 profile 指引

## [3.10.0] - 2026-05-18

### 新增
- 双语 MCP 工具描述（中文 + 英文）
- 带编号菜单选择的双语 setup 向导
- 用于远程部署的 SSE 传输模式
- 基于 token 的鉴权中间件

### 测试
- Round 9 生命周期验证：T1 10/10，T2 20/20

## [3.9.0] - 2026-05-15

### 新增
- 带智能扫描 + 拆分导入的 10 分钟惊喜上手
- setup 期间自动检测既有 AI 工具配置

## [3.8.1] - 2026-05-13

### 修复
- AI 上下文注入不再污染过期检测

## [3.8.0] - 2026-05-12

### 新增
- 知识生命周期工具：`review_knowledge`、`get_stale_knowledge`
- `get_decisions` 和 `add_decision` 上的 domain 参数
- 多标签 domain 支持（逗号分隔）

### 测试
- Round 7：domain 软化 T1 15/15，T2 19/20
- Round 8：decisions domain T1 8/8，T2 20/20

## [3.7.0] - 2026-05-09

### 新增
- 优化的工具描述与工作流快捷方式（39 个工具）
- Round 6 全覆盖基准：39 个工具，88 个场景，98.9% 准确率

## [3.6.0] - 2026-05-07

### 修复
- 在冷启动上下文与身份卡中包含 decisions

## [3.5.1] - 2026-05-05

### 新增
- 上手种子知识、MCP 工具分层、收窄 ICP

## [3.5.0] - 2026-05-03

### 新增
- 将定位锐化为 AI 身份层

## [3.4.0] - 2026-04-30

### 新增
- 个人知识卡（PKC）导出与身份卡改进

## [3.3.0] - 2026-04-27

### 新增
- 所有读/写操作的审计日志

## [3.2.0] - 2026-04-24

### 新增
- 对敏感 profile 字段的静态加密（AES-256-GCM）

## [3.1.0] - 2026-04-21

### 新增
- 信任边界与受限字段

## [3.0.0] - 2026-04-18

### 变更
- 重大架构重写：MCP 原生、模块化核心引擎
- 知识以结构化 JSON 存储（lessons + decisions）

## [2.9.0] - 2026-04-15

### 新增
- 加权多词搜索 + `find_similar_knowledge`

## [2.6.0] - 2026-04-12

### 新增
- 加权搜索评分

## [2.5.0] - 2026-04-09

### 新增
- 批量知识导入 + 笔记摄取

## [2.4.0] - 2026-04-06

### 新增
- 双向知识链接

## [2.3.0] - 2026-04-03

### 新增
- 知识质量：老化、digest、报告导出

## [2.2.0] - 2026-03-31

### 新增
- 原子写入、文件锁、restricted_fields 强制执行

## [2.1.0] - 2026-03-28

### 新增
- 知识搜索、生命周期管理、健康报告

## [2.0.0] - 2026-03-25

### 新增
- 首次发布：带 profile、工作风格、lessons、decisions 的 AI 身份层
- 带 stdio 传输的 MCP server
- Apache 2.0 许可证
