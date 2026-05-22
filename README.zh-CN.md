<div align="center">

<img src="assets/social_preview_zh.png" alt="Engram — 你的 AI 身份层" width="640">

# Engram

### AI 认识你的代码，但不认识你。Engram 解决这个问题。

**一个本地身份层，让每个 AI 工具都从同一个起点认识你。**

`一次写入` · `所有 AI 共享读取` · `本地优先`

[中文](README.zh-CN.md) | [ENGLISH](README.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/piia-engram)](https://pypi.org/project/piia-engram/)

</div>

---

> **TL;DR：** Engram 是一个本地身份层——不是会话记忆，不是 Agent 框架，不是云端数据库。它把你是谁（画像、偏好、经验教训、关键决策）以本地 JSON 文件存储在你自己的电脑上，通过 MCP 让每个 AI 工具读取同一个你。一次写入，所有 AI 共享读取。本地优先，Apache 2.0。

---

AI 工具都很聪明，但它们不认识你。

每次开一个新对话框，你就被忘了。换个工具，又要从头自我介绍。工具一更新，之前设好的偏好可能直接没了。

这是因为现在所有 AI 的记忆都绑在各自的平台上。记忆属于平台，不属于你。平台改了、升了、换了，你的上下文就没了。

**Engram 给你一个独立于任何 AI 工具的本地身份层。** 你告诉它一次你是谁、你怎么工作、你学到了什么。之后不管你开多少个新对话、用哪个工具、工具怎么更新，AI 开口就认识你。

> **Engram 不是 Agent 记忆数据库。** Mem0、Zep、Letta 等工具存的是任务上下文和会话历史。Engram 存的是**你这个人**——你的身份、偏好、经验教训和关键决策。这是不同的一层：不是"这次任务做了什么"，而是"所有任务背后的人是谁"。

## 谁在用 Engram

Engram 为同时使用多个 AI 编程工具、厌倦重复自我介绍的开发者而生。

**如果你在 Claude Code、Codex、Cursor 之间切换** — 代码标准、架构决策、踩过的坑，每次都要重讲。Engram 让每个工具从同一个起点认识你。

**如果你每周开 10+ 个 AI 对话框** — 每一个都从零开始。Engram 让每次对话从第一条消息就有你的完整上下文。

**如果你因为工具更新丢过偏好** — 你的身份存在自己电脑里，不在任何平台内部。更新、重置、迁移都不影响你的记忆。

<details>
<summary><strong>更多使用场景</strong></summary>

**投资分析师**
决策做了，但推理链丢了。Engram 存下每个决策的完整推理，六个月后"我当时为什么放弃那个机会"有真实答案。你的分析框架，不只是笔记，会跟着你进入每一次新分析。

**系统架构师**
架构决策需要上下文：选了什么、排除了什么、为什么。这些内容在 Wiki 里没人读，在记忆里会消失。Engram 保存活的架构决策记录，跨公司、跨项目可检索，AI 在你设计下一个系统时可以直接调用。

**后端开发者**
第三方 API 的坑、集成的隐患、性能权衡——这些隐性知识原本只活在你脑子里，换工作就归零。Engram 把它们变成可搜索的知识库，在新项目遇到同类问题时主动提醒你。

**前端与设计**
你的设计哲学、真实用户反馈带来的 UX 教训、组件选型背后的理由，很少能以 AI 工具能用的方式记录下来。Engram 把这些存成可供 AI 调用的知识，每个新项目都从上一个结束的地方继续。

**Vibe 编程用户**
你用 AI 快速构建，每次开新会话却要重头解释：你的技术栈、你的风格偏好、你不想要的写法。Engram 让每个工具从第一条消息就认识你——同样的栈、同样的模式、同样的语气，不用再重复自己。

</details>

## Engram 不只是存储

大多数记忆工具是被动的：你放进去，它给你取出来。Engram 还是主动的。

**跨项目知识继承**  
描述一个新项目，`get_knowledge_inheritance` 从你所有过往工作中自动提炼最相关的教训和决策，给你一份定制化的起步知识包。第十个项目从前九个的积累中受益——一个工具调用即可获取。

**被动知识捕获**  
把一次会话的摘要粘贴给 `extract_session_insights`，Engram 提取并存储其中的教训和决策。不需要手动记笔记，知识通过日常 AI 对话自然积累。

**不支持 MCP 的工具也能用**  
ChatGPT、Gemini、Kimi 没有 MCP 接口。`get_identity_card` 导出一张即粘即用的 Markdown 身份卡，你的 AI 上下文连不能直接连接的工具也能用上。

**知识健康与发现**  
`get_knowledge_overview` 找出久未复查的知识（30 天以上），给出健康度评分，提示哪些内容值得重新确认。`find_similar_knowledge` 找出重叠条目方便合并，`link_knowledge` 把相关教训和决策串联成可导航的知识网络。

## 快速开始

```bash
pip install piia-engram
engram setup
```

安装向导会自动完成：
1. 检测 Python 环境
2. 发现并配置你的 AI 工具（Claude Code、Cursor、Claude Desktop）
3. 引导你录入种子知识（角色、技术栈、语言）
4. 智能导入你已有的 `CLAUDE.md` / `.cursorrules` 规则文件

设置完成后重启 AI 工具。第一次对话会自动调用 `get_user_context`——AI 已经认识你了。

<details>
<summary><strong>手动 MCP 配置</strong></summary>

如果你更喜欢手动配置，添加到你的 AI 工具 MCP 配置中：

```json
{
  "mcpServers": {
    "engram": {
      "command": "python",
      "args": ["/path/to/engram/src/engram_core/mcp_server.py"]
    }
  }
}
```

</details>

## 升级

```bash
pip install --upgrade piia-engram
```

升级后，Engram 会在下次启动时自动迁移旧版 MCP 配置，无需手动操作。如果 AI 工具仍然显示"MCP 断开连接"，运行：

```bash
engram doctor        # 查看问题所在
engram doctor --fix  # 一步自动修复
```

修复后重启对应的 AI 工具即可。`doctor` 命令会扫描 Claude Code、Cursor、Claude Desktop 的配置文件，移除过时的 server 条目并修复失效路径。

## 远程部署

在自己的服务器上运行 Engram，从任何地方连接使用。

### 服务器配置

```bash
# 安装（含远程支持）
pip install piia-engram[remote]

# 生成认证 token
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 保存输出，例如 "abc123..."

# 以 SSE 模式启动
ENGRAM_AUTH_TOKEN=abc123... python -m engram_core.mcp_server --transport sse --host 0.0.0.0 --port 8767
```

### 客户端配置（Claude Code）

```json
{
  "mcpServers": {
    "engram": {
      "url": "http://你的服务器:8767/sse",
      "headers": {
        "Authorization": "Bearer abc123..."
      }
    }
  }
}
```

### 客户端配置（Cursor）

```json
{
  "mcpServers": {
    "engram": {
      "url": "http://你的服务器:8767/sse",
      "headers": {
        "Authorization": "Bearer abc123..."
      }
    }
  }
}
```

**安全提醒：**
- 生产环境务必使用 HTTPS，放在 nginx/caddy 反向代理后面并配置 TLS。
- 认证 token 保护你的身份数据，请妥善保管。
- 默认绑定 `127.0.0.1`，仅本地可访问；`0.0.0.0` 仅在反向代理后使用。
- 设置 `ENGRAM_CORS_ORIGINS` 限制跨域访问（如 `https://your-domain.com`）。
- 数据始终在你自己的服务器上，不经过任何第三方云。

## 它解决什么

| 没有 Engram | 有 Engram |
|------------|-----------|
| 新对话 = 从零开始 | 每次对话都已经认识你 |
| 工具一更新，偏好可能没了 | 身份存在你电脑里，任何更新都不影响 |
| 换工具要重新自我介绍 | Claude Code、Codex、Cursor 共享同一套记忆 |
| 踩过的坑下次还会踩 | 经验教训跨工具、跨会话持续有效 |
| 记忆锁死在某个平台 | JSON 文件存本地，可读可编辑可迁移 |

## 对比

| 特性 | Engram | Claude Memory | 手动 CLAUDE.md | Mem0 |
|------|--------|--------------|----------------|------|
| 跨工具共享 | 支持 | 仅 Claude | 仅单工具 | 支持 |
| 本地存储 | 全部本地 | 云端 | 本地 | 云端 |
| 数据可编辑 | JSON/MD 直接编辑 | 不可见 | 可编辑 | API |
| MCP 标准协议 | 支持 | 不适用 | 不适用 | 支持 |
| 自动知识积累 | add_lesson/add_decision | 自动 | 手动 | 自动 |
| 可迁移/备份 | 复制文件夹 | 不可导出 | 复制文件 | API 导出 |
| 身份卡导出 | Markdown 卡片 | 无 | 无 | 无 |
| 模型无关 | 任何 MCP 客户端 | 仅 Claude | 取决于工具 | 多模型 |
| 价格 | 免费开源 | 含在订阅中 | 免费 | 免费/付费 |

## 核心功能

| 功能 | 说明 |
|------|------|
| **冷启动上下文** | 新对话开始时调用 `get_user_context`，AI 立即了解你 |
| **经验教训** | `add_lesson` 记录可复用经验，按领域分类，跨工具共享 |
| **关键决策** | `add_decision` 记录选择和理由，保持长期一致性 |
| **知识输入提速** | 批量写入经验/决策，并从自由文本笔记中提取知识 |
| **用户画像** | 角色、语言、技术水平、工作偏好、质量标准 |
| **项目快照** | 按项目保存上下文，新任务快速接续 |
| **信任边界** | 可从冷启动上下文中过滤指定画像字段 |
| **身份卡导出** | 生成 Markdown 卡片，粘贴到不支持 MCP 的 AI |
| **OpenClaw 兼容** | 导入/导出 SOUL.md、MEMORY.md、USER.md |
| **完整备份** | 一键导出/导入全部数据 |
| **来源追踪** | 每条知识记录来自哪个工具 |
| **知识质量** | 发现久未复查的知识，生成摘要和 Markdown 报告 |
| **知识关联** | 让经验教训和关键决策互相引用，形成知识网络 |

### Tier-1 核心工具（10 个 — 日常工作流）

| 工具 | 功能 |
|------|------|
| `get_user_context` | 冷启动：加载身份 + 知识上下文 |
| `wrap_up_session` | 会话结束：提取知识 + 同步 |
| `add_lesson` | 记录可复用的经验教训 |
| `add_decision` | 记录关键决策及理由 |
| `search_knowledge` | 多词加权搜索知识 |
| `get_relevant_knowledge` | 按当前项目检索相关知识 |
| `get_identity_card` | 导出 Markdown 身份卡（给无 MCP 工具用） |
| `update_identity` | 更新身份画像、偏好或质量标准 |
| `get_project_context` | 读取项目快照 |
| `save_project_snapshot` | 保存项目状态 |

默认只加载以上 10 个核心工具。在 MCP 配置的 `env` 中设置 `ENGRAM_TOOLS=all` 可解锁全部 43 个工具。

### Tier-2 高级工具（33 个 — 知识管理、审查、导入导出）

<details>
<summary>点击展开完整工具列表</summary>

| 工具 | 功能 |
|------|------|
| `get_profile` | 读取身份画像（默认 safe 模式） |
| `get_work_style` | 读取工作方式 |
| `get_preferences` | 读取沟通与工作流偏好 |
| `get_trust_boundaries` | 读取信任边界 |
| `get_quality_standards` | 读取质量标准 |
| `get_lessons` | 列出经验教训 |
| `get_decisions` | 列出关键决策 |
| `get_domains` | 读取领域经验图谱 |
| `get_knowledge_inheritance` | 根据描述生成跨项目知识继承包 |
| `list_projects` | 列出所有项目快照 |
| `extract_session_insights` | 从文本中提取经验和决策 |
| `bulk_add_knowledge` | 批量添加经验或决策 |
| `ingest_notes` | 从自由文本笔记提取结构化知识 |
| `update_knowledge` | 更新一条知识（自动检测类型） |
| `archive_knowledge` | 归档一条知识 |
| `review_knowledge` | 标记知识已复习 |
| `merge_knowledge` | 合并重复知识条目 |
| `link_knowledge` | 建立知识间双向关联 |
| `unlink_knowledge` | 移除知识间双向关联 |
| `get_knowledge_overview` | 知识概览（摘要 + 健康度 + 过期检查） |
| `get_related_knowledge` | 查询关联知识 |
| `find_similar_knowledge` | 按内容查找相似知识 |
| `get_stale_knowledge` | 列出需要复习的过期知识 |
| `export_knowledge_report` | 导出 Markdown 知识报告 |
| `request_outline_review` | 生成交互式 HTML 知识审查页面 |
| `apply_review` | 处理审查结果（晋升/归档暂存条目） |
| `export_engram` | 导出完整备份 |
| `import_engram` | 导入备份 |
| `export_engram_to_openclaw` | 导出 OpenClaw 格式 |
| `import_engram_from_openclaw` | 导入 OpenClaw 格式 |
| `read_web_content` | 读取网页内容（需 Reader 服务） |
| `get_audit_log` | 查询审计日志 |
| `start_project` | 新项目启动（继承知识 + 建档） |

</details>

## 数据格式

Engram 的数据全部存储在本地 `~/.engram/`，使用 JSON/Markdown 格式：

```text
~/.engram/
├── schema_version.json          # Schema 版本
├── identity/
│   ├── profile.json             # 你是谁
│   ├── preferences.json         # 你怎么工作
│   ├── quality_standards.json   # 什么算"好"
│   └── trust_boundaries.json    # 谁能看什么
├── knowledge/
│   ├── lessons.json             # 经验教训
│   ├── decisions.json           # 关键决策
│   └── domains.json             # 领域经验
├── projects/
│   └── {project_id}.json        # 项目快照
├── exports/                     # 备份和导出
└── compat/
    └── openclaw/                # OpenClaw 兼容格式
```

所有文件都可以直接打开、编辑、备份、迁移。记忆是你的资产，不是平台的数据。

## 兼容的 AI 工具

| 工具 | 接入方式 | 状态 |
|------|---------|------|
| Claude Code | MCP (stdio) | 已验证 |
| Codex | MCP (stdio) | 已验证 |
| Cursor | MCP (stdio) | 应兼容 |
| Claude Desktop | MCP (stdio) | 应兼容 |
| OpenClaw | SOUL.md/MEMORY.md 导入导出 | 已验证 |
| ChatGPT / Kimi / Gemini | 粘贴身份卡 | 可用 |

## 诞生故事

Engram 是一个人和 AI 一起做出来的。

创始人用 Claude Code 和 Codex 并行工作，AI 帮他写代码，他帮 AI 记住自己。做着做着发现：这个"帮 AI 记住我"的部分，本身就是一个产品。

所以 Engram 从第一天起就是自己的用户——它的代码、架构决策、经验教训，全部存在 Engram 里，被两个 AI 工具共享读取。

## Built With

Engram 由人驱动，AI 工具辅助开发：

| | 角色 |
|------|------|
| [@Patdolitse](https://github.com/Patdolitse) | 创始人 · 产品方向 · 战略决策 · 版权所有者 |
| Claude Code | AI 开发工具 — 架构设计 · 任务规划 · 代码审查 |
| Codex | AI 开发工具 — 代码执行 · 测试 · CI 构建 |

## 常见问题 FAQ

**Engram 是什么？**
Engram 是一个本地优先的 AI 身份层——不是会话记忆，不是 Agent 框架。它把你是谁、你怎么工作、你学到了什么、你做过哪些决策，以本地 JSON 文件保存在你自己的电脑上。每个 MCP 兼容的 AI 工具（Claude Code、Codex、Cursor）都读取同一个身份，新对话、工具更新、换工具都不会丢失你的上下文。

**Engram 和 Mem0、Zep、Letta 等 Agent 记忆工具有什么区别？**
那些工具存的是 Agent 的任务上下文和会话历史——一次工作流中发生了什么。Engram 存的是"你这个人"——你的身份、偏好、经验教训和关键决策。这是不同的一层：身份跨工具、跨会话、跨项目持续有效，而任务记忆的范围是单次 Agent 运行。数据是你自己的本地 JSON 文件，可直接编辑。

**支持哪些 AI 工具？**
任何支持 MCP 协议的工具：Claude Code、OpenAI Codex、Cursor、Claude Desktop 等。不支持 MCP 的工具（ChatGPT、Gemini、Kimi），可以导出 Markdown 身份卡手动粘贴。

**如何安装 Engram？**
```bash
pip install piia-engram
engram setup
```
安装向导会自动检测 AI 工具并配置 MCP。设置完成后重启 AI 工具，AI 会在每次新对话开始时调用 `get_user_context` 认识你。

**升级后 AI 工具显示"MCP server disconnected"，怎么解决？**
在终端运行 `engram doctor --fix`，然后重启 AI 工具。该命令会扫描所有已知 MCP 配置（Claude Code、Cursor、Claude Desktop），移除旧版 server 条目并修复失效路径，一步完成。Engram 在下次 server 启动时也会自动执行此迁移，大多数用户不会遇到这个问题。

**Engram 会把数据发到云端吗？**
所有数据存在本地 `~/.engram/` 目录，Engram 本身不会上传数据到任何地方。可选工具 `read_web_content` 会向本地 Reader 服务（`localhost:7890`）发起请求，该服务可能进一步抓取外部网页——但此工具只有在你显式调用时才执行。身份和知识类核心工具均不发起网络请求。

**Engram 有多少个 MCP 工具？**
43 个 MCP 工具，覆盖身份管理、经验教训、关键决策、项目快照、批量输入、笔记摄入、会话洞见提取、加权知识搜索、相似知识发现、摘要、报告、知识关联、知识合并、生命周期复习、健康度检查、工作流快捷操作和审计日志。

**Engram 免费吗？**
是的。Engram 是 Apache 2.0 开源项目，完全免费。

## 局限性说明

Engram 可以正常使用，但以下功能目前尚未实现：

| 方面 | 当前状态 | 计划版本 |
|---|---|---|
| **文件安全** | JSON 写入使用 portalocker 文件锁 + 原子替换 | 后续补充更大并发压力测试 |
| **访问控制** | `restricted_fields` 会从 `get_user_context` 和 `get_profile(safe=true)` 中过滤画像字段 | MCP 不传调用方身份，暂不做复杂 ACL |
| **加密** | 可选字段级 AES-256-GCM 加密，通过 `ENGRAM_SECRET` 环境变量启用。安装 `pip install piia-engram[secure]`。 | 全盘加密（v4.0）|
| **审计日志** | 可选访问审计，通过 `ENGRAM_AUDIT=1` 环境变量启用。日志写入 `~/.engram/audit.log`。 | 按调用方审计（受 MCP 规范限制）|
| **调用方身份** | MCP 协议不传递工具身份 | 受 MCP 规范限制 |
| **并发写保护** | Engram JSON 写入已通过文件锁和原子替换保护 | 网络文件系统等边界场景不保证 |

**实际使用建议：**
- 不要在 Engram 里存密码、API Key、客户隐私数据
- `~/.engram/` 目录下的文件，本机有读权限的进程都可以读取
- `restricted_fields` 能减少冷启动上下文暴露的画像字段，但不是加密，也不是真正的 ACL

这不是劝你不用 Engram —— 而是对它本质的诚实描述：它是一个本地个人 AI 上下文层。用于存储个人偏好、项目决策、技术笔记等内容，今天就可以正常使用。

## 安全配置

### 字段级加密（可选）

加密敏感的用户画像字段（email、phone、location 等）：

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="选一个强口令"
```

加密后的字段以 `enc:v1:...` 格式存储在 JSON 文件中。不设置 `ENGRAM_SECRET` 时，Engram 照常以明文工作（向后兼容）。

### 审计日志（可选）

记录所有读写操作：

```bash
export ENGRAM_AUDIT=1
```

日志以 JSON-lines 格式写入 `~/.engram/audit.log`。可通过 `get_audit_log` 工具或 `grep` 查询。

## Contributing

见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。英文版见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[Apache 2.0](LICENSE) — Engram 是自由软件，记忆属于你。
