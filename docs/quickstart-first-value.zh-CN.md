# 快速上手：约 5 分钟拿到第一个价值

目标：存一条有用的经验，然后在一个全新的 AI 会话里把它回忆出来——只用默认的
**17 个核心工具**。这条路径不需要高级工具，也不需要
`ENGRAM_TOOLS=all`；默认就是 `ENGRAM_TOOLS=core`。

本指南面向本地的 MCP 兼容编程工具，例如 Claude Code、Codex、Cursor、Windsurf
或 Claude Desktop。不同 MCP 宿主的配置细节会有差异。需要针对具体宿主的配置时，
可从 [Claude Code](integrations/claude-code.md)、[Codex](integrations/codex.md)
或 [Cursor](integrations/cursor.md) 开始。工具分层和需 owner 授权的接口，
请对照 [运营 MCP 速查表](operator-mcp-cheatsheet.md)。

## 1. 安装并连接

```bash
pip install piia-engram
engram setup
```

`engram setup` 会检测你的 AI 客户端（Claude Code、Cursor、Claude Desktop、
Codex…），列出它将要修改的**具体配置文件路径**，然后请你**一键确认**后才写入
MCP 连接（每次写入前都会自动备份）。选择"否"则不动任何外部配置文件。
非交互/CI 场景可用 `engram setup --apply-external-config` 跳过确认直接写入。

写入连接后，**自动引导（auto-bootstrap）** 会接手剩下的事：你的 AI 工具第一次
调用 Engram 时（通过 `get_user_context` 或 `get_resume_brief`），会以只读方式扫描
你已有的规则文件（`CLAUDE.md`、`AGENTS.md`、`.cursorrules` 等），自动导入你的偏好
和项目规则——不需要单独的导入步骤。所以上面这一次"连接"就是你全部要做的；
"它已经懂我了"这个时刻会在下一次会话里自己发生。

身份和知识工具使用本地文件，无需任何云账号。

## 2. 检查健康状态

```bash
engram doctor
```

健康的安装会报告 MCP 服务器可用、Engram 数据目录可读。这份报告用于本地诊断；
分享前请先检查，因为私有诊断信息可能包含本地路径。

## 3. 给 Engram 一条稳定的偏好

在你已连接的 AI 工具里，让它记录一条简单偏好，例如：

```text
记住：我偏好简洁的回答，并附上明确的验证命令。
```

AI 可以调用核心写入工具之一：

- `memory_store`
- `add_lesson`
- `add_decision`
- `add_playbook`
- `update_identity`

AI 新建议的知识会经过一道风险门分类：

- **低 / 中风险**（大多数偏好、经验、项目规则）：立即自动通过验证——下次会话
  就能用，无需人工审批。
- **高风险**（凭证值、可执行命令、权限覆盖）：进入 `staging` 待 owner 审核后
  才生效。

这种平衡让"第一个价值"路径保持顺滑，同时保护敏感内容。你随时可以用
`review_staging(action="list")` 查看待审项。

## 4. 在新会话里把它回忆出来

在同一个已配置的工具里开一个全新对话，或者在同机另一个已配置的 MCP 工具里开。
AI 应当调用一个核心读取/启动工具，例如：

- `get_user_context`
- `get_recall`
- `search_knowledge`
- `get_relevant_knowledge`
- `get_resume_brief`

第一个价值时刻很朴素：新会话能直接从你已经给过的偏好或经验出发，而不是再让你
解释一遍。

## 5. 你刚刚用到的核心工具

| 用途 | 核心工具 |
|---|---|
| 启动与恢复 | `get_user_context`、`get_recall`、`get_resume_brief`、`get_recent_context`、`get_daily_log` |
| 读取/搜索 | `search_knowledge`、`get_relevant_knowledge` |
| 写入/更新 | `memory_store`、`add_lesson`、`add_decision`、`add_playbook`、`update_identity` |
| 项目上下文 | `get_project_context`、`save_project_snapshot` |
| 会话结束 | `wrap_up_session` |
| 诊断 | `doctor` |

`get_identity_card` 也在核心层，因为非 MCP 交接经常需要它，但它属于需 owner 授权的
导出接口，而非普通的读取/搜索工具。

高级工具集用于审核、导入/导出、治理、迁移和管理类工作流。大多数初次用户在真正
需要这些工作流之前，把它关着就好。

## 何时启用全部工具

第一个价值、日常回忆、常规会话收尾，保持默认的核心工具集即可。只有当你确实需要
审核队列、导入/导出、Playbook 维护、本地工具注册表管理，或仅做提案的上下文治理
预览时，才启用全部工具：

```bash
ENGRAM_TOOLS=all
```

部分高级工具属于 owner/admin/导出接口。打开它们会增加宿主在工具列表里能看到的
内容；但这不会移除 owner 授权门、治理检查，也不会免除你自己确认公开动作的要求。

## 如果回忆没有触发

先确认客户端能看到 MCP 服务器：运行 `engram doctor`，重启 AI 工具，再让它调用
`get_resume_brief` 或 `get_user_context`。

如果工具已连接但回答仍然忽略记忆，就把回忆显式触发一次：

```text
用 Engram 搜索我保存的那条关于简洁回答的偏好。
```

如果显式搜索能用、但主动回忆不行，就把该客户端当作 L2 读取/搜索能力，而非
L3 或 L4 行为已验证能力。这仍然是有用的第一个价值；只是意味着在一次验证运行
证明更高能力之前，公开宣称应保持在"行为增益"或"跨客户端连续性"等级之下。

## 下一步

- 读完整的 [用户指南](user-guide.zh-CN.md)：安装 → 首个价值 → 跨工具续接 →
  治理/审批 → 隐私/数据主权 → 常见问题。
- 读 [信任证据](trust-evidence.md)，了解公开宣称是如何被检验的。
- 读 [信任模型](trust.md)，了解数据边界和不该存什么。
- 运行 `python demos/cross_tool_continuity_demo.py --json`，得到一份合成的
  跨工具交接证明。
- 读 [对比](comparison.md)，理解 piia-engram 在 agent 记忆数据库、仓库规则文件、
  原生工具记忆之间的定位。
