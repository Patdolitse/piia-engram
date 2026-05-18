<div align="center">

<img src="assets/social_preview.png" alt="Engram — AI 记忆印记" width="640">

# Engram

### AI 记忆印记

**你的 AI 记忆，本地存储，跨工具共享。**

`一次写入` · `所有 AI 共享读取` · `100% 本地`

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

</div>

---

AI 工具都很聪明，但它们不认识你。

每换一个工具、一个会话、一个项目，你都要重新解释自己是谁。Engram 把你的身份、偏好、经验、决策持久化为本地文件，通过 MCP 协议让任何 AI 工具共享读取。

> 不是另一个 Agent OS，而是所有 Agent OS 都应该接入的记忆印记。

## Quick Start

```bash
# 1. 克隆
git clone https://github.com/Patdolitse/engram.git
cd engram

# 2. 安装
pip install -e .

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
| **用户画像** | 角色、语言、技术水平、工作偏好、质量标准 |
| **项目快照** | 按项目保存上下文，新任务快速接续 |
| **信任边界** | 控制哪些工具能访问哪些数据 |
| **身份卡导出** | 生成 Markdown 卡片，粘贴到不支持 MCP 的 AI |
| **OpenClaw 兼容** | 导入/导出 SOUL.md、MEMORY.md、USER.md |
| **完整备份** | 一键导出/导入全部数据 |
| **来源追踪** | 每条知识记录来自哪个工具 |

<details>
<summary><strong>完整 MCP 工具列表（27 个）</strong></summary>

**读取工具：**

| 工具 | 功能 |
|------|------|
| `get_user_context` | 冷启动，加载完整用户上下文 |
| `get_identity_card` | 导出 Markdown 身份卡 |
| `get_profile` | 读取身份画像 |
| `get_work_style` | 读取工作方式 |
| `get_preferences` | 读取偏好（v2.0） |
| `get_trust_boundaries` | 读取信任边界 |
| `get_quality_standards` | 读取质量标准 |
| `get_lessons` | 读取经验教训 |
| `get_decisions` | 读取关键决策 |
| `get_domains` | 读取领域经验图谱 |
| `get_relevant_knowledge` | 按项目检索相关知识 |
| `get_project_context` | 读取项目快照 |
| `list_projects` | 列出所有项目 |
| `get_stats` | 知识资产统计 |

**写入工具：**

| 工具 | 功能 |
|------|------|
| `add_lesson` | 记录经验教训 |
| `add_decision` | 记录关键决策 |
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

## Contributing

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE) - Engram 是自由软件，记忆属于你。
