<div align="center">

<img src="assets/social_preview_zh.png" alt="Engram — 本地 AI 记忆层" width="640">

# Engram

### AI 记忆印记

**你的 AI 记忆，本地存储，跨工具共享。**

`一次写入` · `所有 AI 共享读取` · `100% 本地`

[中文](README.zh-CN.md) | [ENGLISH](README.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/piia-engram)](https://pypi.org/project/piia-engram/)

</div>

---

> **TL;DR：** Engram 是一个 MCP server，给 Claude Code、Codex、Cursor 提供持久身份层——你的画像、偏好、经验教训、关键决策以本地 JSON 文件存储。一次写入，所有 AI 共享读取。100% 本地，Apache 2.0。

---

AI 工具都很聪明，但它们不认识你。

每换一个工具、一个会话、一个项目，你都要重新解释自己是谁。Engram 把你的身份、偏好、经验、决策持久化为本地文件，通过 MCP 协议让任何 AI 工具共享读取。

> 不是另一个 Agent OS，而是所有 Agent OS 都应该接入的记忆印记。

与只记录"本次任务做了什么"的会话记忆工具不同，Engram 存的是**你这个人**——你的身份、偏好、经验教训和关键决策——让每个 AI 工具都从同一个起点认识你。

## 谁在用 Engram

适合所有靠**积累型判断**工作的人——不是一次性任务，而是多年沉淀的标准、决策和经验教训，AI 应该天然就知道。

**开发者**  
你的代码质量标准（测试覆盖率要求、命名规范、哪类 hack 绝对不接受）、架构决策和踩过的坑，原本只活在你脑子里，换个会话就归零。有了 Engram，新项目第一天不是真的第一天——AI 已经知道你的底线。

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

## Engram 不只是存储

大多数记忆工具是被动的：你放进去，它给你取出来。Engram 还是主动的。

**跨项目知识继承**  
描述一个新项目，`get_knowledge_inheritance` 从你所有过往工作中自动提炼最相关的教训和决策，给你一份定制化的起步知识包。第十个项目从前九个的积累中受益——自动完成。

**被动知识捕获**  
把一次会话的摘要粘贴给 `extract_session_insights`，Engram 自动提取并存储其中的教训和决策。不需要手动记笔记，知识在你不刻意思考的时候也在积累。

**不支持 MCP 的工具也能用**  
ChatGPT、Gemini、Kimi 没有 MCP 接口。`get_identity_card` 导出一张即粘即用的 Markdown 身份卡，你的 AI 上下文连不能直接连接的工具也能用上。

**知识健康与发现**  
`get_knowledge_overview` 找出久未复查的知识（90 天以上），给出健康度评分，提示哪些内容值得重新确认。`find_similar_knowledge` 找出重叠条目方便合并，`link_knowledge` 把相关教训和决策串联成可导航的知识网络。

## 本地部署

在自己的电脑上运行 Engram，数据存在 `~/.engram/`，AI 工具通过 stdio 连接。

```bash
# 1. 克隆
git clone https://github.com/Patdolitse/engram.git
cd engram

# 2. 安装
pip install piia-engram      # 从 PyPI 安装（推荐）
# 或从源码安装：pip install -e .

# 3. 初始化你的记忆
python demos/setup_engram.py

# 4. 配置 MCP（复制下面的 JSON 到 ~/.claude/.mcp.json）
```

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

```bash
# 5. 重启 Claude Code，新对话中 AI 会自动调用 get_user_context 认识你
```

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
- 数据始终在你自己的服务器上，不经过任何第三方云。

## 它解决什么

| 没有 Engram | 有 Engram |
|------------|-----------|
| 每次新对话都要解释你是谁 | AI 自动加载你的身份和偏好 |
| 换工具丢失所有积累的上下文 | Claude Code 和 Codex 共享同一套记忆 |
| AI 用英文回复、写冗长注释 | AI 自动适配你的语言和代码风格 |
| 踩过的坑下次还会踩 | 经验教训跨工具共享，不重复犯错 |
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

<details>
<summary><strong>完整 MCP 工具列表（37 个）</strong></summary>

**读取工具：**

| 工具 | 功能 |
|------|------|
| `get_user_context` | 冷启动，加载完整用户上下文 |
| `get_identity_card` | 导出 Markdown 身份卡 |
| `get_profile` | 读取身份画像，可用 safe 模式过滤受限字段 |
| `get_work_style` | 读取工作方式 |
| `get_preferences` | 读取偏好（v2.0） |
| `get_trust_boundaries` | 读取信任边界 |
| `get_quality_standards` | 读取质量标准 |
| `get_lessons` | 读取经验教训 |
| `get_decisions` | 读取关键决策 |
| `get_domains` | 读取领域经验图谱 |
| `get_relevant_knowledge` | 按项目检索相关知识 |
| `get_knowledge_inheritance` | 根据自由文本生成跨项目知识继承包 |
| `get_project_context` | 读取项目快照 |
| `list_projects` | 列出所有项目 |
| `get_stats` | 知识资产统计 |
| `get_knowledge_overview` | 知识概览（摘要 + 健康度 + 过期检查） |
| `get_related_knowledge` | 查询某条知识关联的其他知识 |
| `find_similar_knowledge` | 按内容查找相似知识 |
| `export_knowledge_report` | 导出 Markdown 知识报告 |

**写入工具：**

| 工具 | 功能 |
|------|------|
| `add_lesson` | 记录经验教训 |
| `add_decision` | 记录关键决策 |
| `bulk_add_knowledge` | 批量添加经验教训或决策 |
| `ingest_notes` | 从自由文本笔记提取经验和决策 |
| `extract_session_insights` | 从会话摘要自动提取经验和决策 |
| `link_knowledge` | 建立两条知识的双向关联 |
| `unlink_knowledge` | 移除两条知识的双向关联 |
| `merge_knowledge` | 合并重复知识条目 |
| `update_knowledge` | 更新一条经验教训或决策（自动检测类型） |
| `archive_knowledge` | 废弃一条经验教训或决策（自动检测类型） |
| `update_profile` | 更新身份画像 |
| `update_preferences` | 更新偏好 |
| `update_trust_boundaries` | 更新信任边界 |
| `update_work_style` | 更新工作方式（v1 兼容） |
| `update_quality_standards` | 更新质量标准 |
| `save_project_snapshot` | 保存项目快照 |

**导入导出工具：**

| 工具 | 功能 |
|------|------|
| `export_engram` | 导出完整备份 |
| `import_engram` | 导入备份 |
| `export_engram_to_openclaw` | 导出 OpenClaw 格式 |
| `import_engram_from_openclaw` | 导入 OpenClaw 格式 |
| `read_web_content` | 读取网页内容（需 Reader 服务） |
| `search_knowledge` | 多词加权搜索经验教训和关键决策 |
| `get_audit_log` | 查询最近的审计日志条目 |

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
Engram 是一个本地优先的 MCP server，给 Claude Code、Codex、Cursor 等 AI 工具提供持久身份层。它把你是谁、你怎么工作、你学到了什么、你做过哪些决策——以本地 JSON 文件的形式保存在你的机器上。

**Engram 和其他 AI 记忆工具有什么区别？**
大多数 AI 记忆工具存的是"本次任务做了什么"（会话上下文）。Engram 存的是"你这个人"——你的身份、偏好、经验教训和关键决策，跨工具、跨会话、跨项目持续有效。数据是你自己的本地 JSON 文件，可直接编辑。

**支持哪些 AI 工具？**
任何支持 MCP 协议的工具：Claude Code、OpenAI Codex、Cursor、Claude Desktop 等。不支持 MCP 的工具（ChatGPT、Gemini、Kimi），可以导出 Markdown 身份卡手动粘贴。

**如何安装 Engram？**
```bash
git clone https://github.com/Patdolitse/engram.git
pip install piia-engram
# 或从源码安装：cd engram && pip install -e .
python demos/setup_engram.py
```
配置 MCP 后重启 AI 工具，AI 会在每次新对话开始时自动调用 `get_user_context` 认识你。

**升级后 AI 工具显示"MCP server disconnected"，怎么解决？**
在终端运行 `engram doctor --fix`，然后重启 AI 工具。该命令会扫描所有已知 MCP 配置（Claude Code、Cursor、Claude Desktop），移除旧版 server 条目并修复失效路径，一步完成。Engram 在下次 server 启动时也会自动执行此迁移，大多数用户不会遇到这个问题。

**Engram 会把数据发到云端吗？**
所有数据存在本地 `~/.engram/` 目录，Engram 本身不会上传数据到任何地方。可选工具 `read_web_content` 会向本地 Reader 服务（`localhost:7890`）发起请求，该服务可能进一步抓取外部网页——但此工具只有在你显式调用时才执行。身份和知识类核心工具均不发起网络请求。

**Engram 有多少个 MCP 工具？**
37 个 MCP 工具，覆盖身份管理、经验教训、关键决策、项目快照、批量输入、笔记摄入、会话洞见提取、加权知识搜索、相似知识发现、摘要、报告、知识关联、知识合并、健康度检查和审计日志。

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
