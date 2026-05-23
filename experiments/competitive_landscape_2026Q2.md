# MCP 记忆层竞品图谱 - 2026 Q2

数据采集时间：2026-05-24 04:10-05:35 Asia/Shanghai  
主要来源：GitHub REST API（已登录账号）、PyPI JSON、pypistats、npm Registry、npm downloads API、MCP Registry API、项目 README/官网。  
重要说明：本报告新增“国籍/注册地（公开证据）”字段。对个人开发者不根据姓名、头像、语言、时区推断国籍；只记录公开 GitHub profile/官网明确可见的所在地、组织注册地或公开归属。未公开则标为“未公开”。

## 执行摘要

2026 Q2 的 MCP/AI 记忆层市场已经不是小众工具赛道，而是快速拥挤的基础设施赛道。核心样本 30+ 个项目合计超过 230k GitHub stars（不含 `modelcontextprotocol/servers` 这个官方多 server monorepo），其中 Mem0、MemPalace、agentmemory、memU、MemOS 已经形成强 attention moat。piia-engram 当前 79 stars、3928 PyPI 周下载，规模明显落后，但仍有一块稀缺位置：它不是“agent 自动记忆库”，而是“用户身份、偏好、经验、决策的可治理跨工具记忆”。真正差异化不在“本地优先 + MCP”，这些已经红海；而在 staging -> verified、人类审批、结构化 lesson/decision/playbook、可移植身份卡和跨工具个人身份层。

## 市场概览

### 市场规模

- GitHub 搜索 `mcp memory`、`mcp-memory`、`agent memory`、`persistent memory mcp`、`engram mcp` 后，发现活跃相关项目远超原方案中的 10 个。
- 重点样本 37 个项目中，直接或相邻记忆项目合计约 230k+ stars；如果加入官方 `modelcontextprotocol/servers` monorepo，则总关注度超过 310k stars。
- MCP Registry `memory` 搜索返回 100 条记录（接口 limit=100），其中大量是远程商业 server、垂直领域 memory、代码库 memory、知识图谱 memory 和实验性项目。
- 下载量层面，Mem0 PyPI 周下载 742446，Graphiti Core 周下载 146798，Basic Memory 周下载 16651，mcp-memory-service 周下载 9842，piia-engram 周下载 3928。部分包因 pypistats 限流未能采到周下载。
- npm 层面，`@modelcontextprotocol/server-memory` 周下载 226211，`byterover-cli` 周下载 2150，`@remnic/core` 周下载 1959，`@malindar/whyline` 周下载 1701，`@danielmarbach/mnemonic-mcp` 周下载 877。

### 市场分层图

X 轴：自动捕获 <- -> 用户治理  
Y 轴：单工具 <- -> 跨工具

| 象限 | 代表项目 | 判断 |
|---|---|---|
| 自动捕获 + 跨工具 | MemPalace, agentmemory, mcp-memory-service, ByteRover, MemSearch, ClawMem, Vestige, Remnic, Stash | 当前主战场。主张“AI 自动捕获、自动整合、自动召回”，用户负担低，但容易产生错误记忆和不可审计问题。 |
| 自动捕获 + 单工具 | claude-memory-compiler, claude-mem-lite, Claude Code hooks 生态, Cursor/Windsurf 原生记忆 | 体验闭环最强，因为可用 hooks/原生入口绕过“模型不主动调用工具”的问题。缺点是平台绑定。 |
| 用户治理 + 跨工具 | piia-engram, OneNomad/przm-memory, Remnic（部分）, TradeMemory Protocol（垂直）, VaultCrux（Registry） | 稀缺象限。piia-engram 的 staging -> verified 和用户审批在这里最有辨识度。 |
| 用户治理 + 单工具/项目 | Basic Memory, Context Portal, Memory Bank MCP, Verified Repo Memory, codebase-memory-mcp | 更偏项目知识库、repo memory 或代码库 context，不完全解决“用户身份跨工具”。 |

### 关键趋势

1. **本地优先已经不是差异化**：MemPalace、Gentleman Engram、Basic Memory、mcp-memory-service、Wax、Vestige、Remindb 都在讲 local-first。
2. **跨工具 MCP 也在快速商品化**：Claude Code、Cursor、Codex、Windsurf、OpenCode、VS Code、JetBrains 等客户端支持被大量项目列入 README。
3. **语义/混合检索成为默认预期**：向量、BM25/FTS、知识图谱、多信号 fusion 已经普遍；piia-engram 的 n-gram 检索轻量但在“benchmark narrative”上吃亏。
4. **hooks 是解决主动调用问题的关键路径**：Claude Code hooks 生态正在让“记忆自动捕获”变成现实，而非依赖模型自觉调用 MCP 工具。
5. **治理、审批、可纠错仍是空白**：多数竞品强调自动存、自动整合、自动 recall，少数有 review/correction，但很少把用户审批作为核心产品机制。
6. **命名冲突严重**：Engram/Memory/AgentMemory/Mem* 名字高度拥挤，`piia-` 前缀必要，但 SEO 和用户认知仍需强化 PIIA 品牌。

## 竞品数据卡

### 1. Mem0

```yaml
name: Mem0
github_url: https://github.com/mem0ai/mem0
stars: 56520
forks: 6443
open_issues: 409
created: 2023-06-20
last_commit: 2026-05-23
license: Apache-2.0
language: Python
install_method: pip / npm / SDK / cloud
transport: SDK/API; MCP 通过第三方或集成层
storage: vector DB / graph / cloud or self-host backend
search: semantic + BM25 + entity + temporal multi-signal retrieval
auto_capture: 强，主张自动抽取和自动存储
governance: 未见 staging -> verified 式用户审批
knowledge_types: memory facts / entities / graph
cross_tool: Claude Code, Cursor, Codex, Windsurf 等通过集成或 SDK
hook_mechanism: 非核心卖点
tagline: Universal memory layer for AI Agents
target_user: AI agent builders / app developers
pricing: OSS + cloud
unique_claim: 生产级 memory algorithm，README 自称 LongMemEval 94.8
pypi_weekly_downloads: 742446
npm_weekly_downloads: 未采集
longmemeval_score: README 自称 94.8
developer_nationality_or_registry: 组织公开所在地 United States of America；国籍不适用/未公开
sources: GitHub, PyPI, pypistats
```

判断：Mem0 是 agent memory 的事实头部。它不是 piia-engram 的同型产品，但会吸走“AI memory”搜索心智和 benchmark 叙事。

### 2. MemPalace

```yaml
name: MemPalace
github_url: https://github.com/MemPalace/mempalace
stars: 52712
forks: 6955
open_issues: 545
created: 2026-04-05
last_commit: 2026-05-23
license: MIT
language: Python
install_method: uv tool / pipx / pip
transport: MCP stdio; CLI
storage: ChromaDB default; pluggable backend; verbatim text store
search: semantic search; palace/wing/room/drawer structure
auto_capture: 强，特别强调 Claude Code hooks 和 session retention
governance: 未见用户审批机制
knowledge_types: conversation/project text, palace hierarchy
cross_tool: README 主要强调 Claude Code；MCP 可扩展
hook_mechanism: 是，README 首屏强调 hooks
tagline: Local-first AI memory. Verbatim storage, pluggable backend, LongMemEval 96.6% R@5 raw
target_user: Claude Code / coding-agent power users
pricing: 免费 OSS
unique_claim: 不总结、不改写，存原文；LongMemEval 96.6% R@5 raw
pypi_weekly_downloads: pypistats 限流未采到
npm_weekly_downloads: mempalace 172
longmemeval_score: README 自称 96.6% R@5 raw
developer_nationality_or_registry: GitHub org 未公开；官网 linktree，无国籍/注册地证据
sources: GitHub, PyPI JSON, npm
```

判断：直接威胁极高。MemPalace 把“hooks + Claude Code retention + benchmark”讲得非常强，且 attention 已经极大。

### 3. agentmemory

```yaml
name: agentmemory
github_url: https://github.com/rohitg00/agentmemory
stars: 16746
forks: 1379
open_issues: 163
created: 2026-02-25
last_commit: 2026-05-22
license: Apache-2.0
language: TypeScript
install_method: npm / likely CLI
transport: MCP
storage: README flags show SQLite/Postgres/vector/knowledge graph
search: hybrid semantic / FTS / graph
auto_capture: 强，定位为 coding agents persistent memory
governance: 未见 piia 式审批；有 review 相关描述
knowledge_types: agent memory / documents / knowledge graph
cross_tool: Claude Code, Cursor, Codex, Windsurf 等
hook_mechanism: 是，README 包含 hooks
tagline: #1 Persistent memory for AI coding agents based on real-world benchmarks
target_user: AI coding agent users
pricing: OSS
unique_claim: real-world benchmarks + zero-config cross-client
pypi_weekly_downloads: pypistats 限流未采到
npm_weekly_downloads: 未采集
longmemeval_score: README 含 LongMemEval 关键词，需进一步核验具体分数
developer_nationality_or_registry: 个人 GitHub 公开所在地 London, UK；国籍未公开
sources: GitHub, PyPI JSON
```

判断：最需要重视的新竞品之一。它覆盖 piia-engram 的跨工具开发者场景，并用 benchmark 和零配置叙事争夺入口。

### 4. memU

```yaml
name: memU
github_url: https://github.com/NevaMind-AI/memU
stars: 13691
forks: 1027
open_issues: 109
created: 2025-07-29
last_commit: 2026-04-22
license: GitHub API NOASSERTION; README badge Apache-2.0
language: Python
install_method: pip package memu-py
transport: SDK/API; OpenClaw/agent integration
storage: vector / knowledge graph oriented
search: semantic / graph
auto_capture: 强，24/7 proactive agents
governance: 未见审批机制
knowledge_types: memory primitives / agent memory
cross_tool: 主要面向 proactive agents 和 OpenClaw
hook_mechanism: README 包含 hooks 关键词
tagline: 24/7 Always-On Proactive Memory for AI Agents
target_user: proactive agent builders
pricing: OSS + possible service
unique_claim: proactive always-on memory
pypi_weekly_downloads: memu-py 702
longmemeval_score: 未见明确分数
developer_nationality_or_registry: GitHub org 未公开；官网 nevamind.ai，无国籍/注册地证据
sources: GitHub, PyPI JSON, pypistats
```

判断：不是同型个人身份层，但其规模说明 agent memory 基础设施已成热门赛道。

### 5. MemOS

```yaml
name: MemOS
github_url: https://github.com/MemTensor/MemOS
stars: 9353
forks: 846
open_issues: 182
created: 2025-07-06
last_commit: 2026-05-22
license: Apache-2.0
language: TypeScript
install_method: repo/SDK
transport: MCP present in README
storage: memory OS abstraction; SQLite/vector/Qdrant signals in README
search: hybrid retrieval
auto_capture: 强，自演化/跨任务 reuse
governance: 未见用户审批
knowledge_types: memory blocks / skill reuse
cross_tool: agent framework oriented
hook_mechanism: 未见作为核心
tagline: Self-evolving memory OS for LLM & AI Agents
target_user: agent infrastructure developers
pricing: OSS
unique_claim: ultra-persistent memory, hybrid retrieval, 35.24% token savings
pypi_weekly_downloads: memos 517（包名可能非一一对应，需谨慎）
longmemeval_score: README 包含 LongMemEval 关键词
developer_nationality_or_registry: GitHub org 名 OpenMem，所在地未公开
sources: GitHub, PyPI
```

判断：上层概念更大，不是 piia-engram 的直接替代，但会影响“memory OS”认知。

### 6. EverOS

```yaml
name: EverOS
github_url: https://github.com/EverMind-AI/EverOS
stars: 5588
forks: 592
open_issues: 93
created: 2025-10-28
last_commit: 2026-05-21
license: Apache-2.0
language: Python
install_method: Python library / API
transport: 未明确 MCP 为核心
storage: vector/embedding oriented
search: semantic
auto_capture: 强，self-evolving agents
governance: 未见用户审批
knowledge_types: agent long-term memory
cross_tool: Claude Code keyword present,但跨工具不是核心叙事
hook_mechanism: 未见核心
tagline: Build, evaluate, and integrate long-term memory for self-evolving agents
target_user: agent memory researchers / builders
pricing: OSS/API unknown
unique_claim: long-term memory eval/integration
pypi_weekly_downloads: everos 192
longmemeval_score: README 包含 LongMemEval 关键词
developer_nationality_or_registry: GitHub org 未公开；官网 EverMind.ai，无注册地证据
sources: GitHub, PyPI, pypistats
```

判断：偏 agent memory/eval，相邻但不直接。

### 7. ByteRover CLI（formerly Cipher）

```yaml
name: ByteRover CLI
github_url: https://github.com/campfirein/byterover-cli
stars: 4781
forks: 455
open_issues: 15
created: 2025-06-19
last_commit: 2026-05-23
license: GitHub API NOASSERTION; npm Elastic-2.0
language: TypeScript
install_method: npm package byterover-cli
transport: MCP
storage: portable memory layer; details需进一步核验
search: FTS / graph signals in README flags
auto_capture: 强，autonomous coding agents
governance: 有 review 关键词，但未见 staging -> verified 机制
knowledge_types: coding-agent memory
cross_tool: Claude Code, Cursor, Windsurf 等
hook_mechanism: 未见明确 hooks 核心
tagline: The portable memory layer for autonomous coding agents
target_user: autonomous coding agent users
pricing: npm Elastic-2.0，可能非纯 OSS
unique_claim: portable memory layer
npm_weekly_downloads: 2150
developer_nationality_or_registry: GitHub org 未公开
sources: GitHub, npm
```

判断：与 piia-engram 的“portable memory”方向有重叠，需要持续观察其格式/迁移策略。

### 8. Gentleman-Programming/engram

```yaml
name: Gentleman Programming Engram
github_url: https://github.com/Gentleman-Programming/engram
stars: 3711
forks: 441
open_issues: 119
created: 2026-02-16
last_commit: 2026-05-20
license: MIT
language: Go
install_method: brew / single binary / plugin
transport: MCP stdio / HTTP API / CLI / TUI
storage: SQLite + FTS5
search: FTS5; README mentions semantic/graph docs
auto_capture: 中，agent calls mem_save after significant work
governance: 未见审批机制
knowledge_types: title, type, What/Why/Where/Learned; project memory
cross_tool: Claude Code, OpenCode, Gemini CLI, Codex, VS Code, Antigravity, Cursor, Windsurf
hook_mechanism: 有 plugin/setup，但 hooks 不是核心字段
tagline: One brain. Local or cloud. Agent-agnostic, single binary, zero dependencies.
target_user: AI coding agent users
pricing: OSS + Engram Cloud
unique_claim: Go single binary, SQLite+FTS5, broad agent setup
developer_nationality_or_registry: GitHub org 未公开
sources: GitHub
```

判断：命名冲突最大，也是跨工具本地记忆的直接竞品。它的安装体验和单 binary 叙事比 piia-engram 更简单。

### 9. Basic Memory

```yaml
name: Basic Memory
github_url: https://github.com/basicmachines-co/basic-memory
stars: 3071
forks: 206
open_issues: 68
created: 2024-12-02
last_commit: 2026-05-23
license: AGPL-3.0
language: Python
install_method: pip
transport: MCP
storage: Markdown files + local knowledge graph; SQLite/Postgres signals
search: semantic + graph
auto_capture: 中，LLM sync / local knowledge management
governance: 用户可编辑 Markdown，但未见 staging -> verified
knowledge_types: notes/entities/relations
cross_tool: Claude Code, Cursor, Codex 等
hook_mechanism: 未见核心
tagline: AI conversations that actually remember
target_user: local-first knowledge management users
pricing: OSS AGPL
unique_claim: Zettelkasten + knowledge graph + Markdown-first
pypi_weekly_downloads: 16651
developer_nationality_or_registry: GitHub org 未公开；官网 basicmachines.co，无注册地证据
sources: GitHub, PyPI, pypistats, MCP Registry
```

判断：更像“AI 可读个人知识库”，不完全替代 piia-engram，但对 Markdown-first 用户有吸引力。

### 10. codebase-memory-mcp

```yaml
name: codebase-memory-mcp
github_url: https://github.com/DeusData/codebase-memory-mcp
stars: 2556
forks: 281
open_issues: 110
created: 2026-02-24
last_commit: 2026-05-23
license: MIT
language: C
install_method: static binary / PyPI package codebase-memory-mcp
transport: MCP
storage: persistent codebase knowledge graph
search: code intelligence graph / sub-ms queries
auto_capture: indexes codebases
governance: repo facts, not user memory governance
knowledge_types: code symbols / relationships / repo intelligence
cross_tool: Claude Code, Codex present in README flags
hook_mechanism: hooks present
tagline: High-performance code intelligence MCP server
target_user: coding agents needing repo memory
pricing: OSS
unique_claim: 155 languages, average repo in milliseconds, 99% fewer tokens
pypi_weekly_downloads: pypistats 限流未采到
developer_nationality_or_registry: 个人 GitHub 公开所在地 Berlin；国籍未公开
sources: GitHub, PyPI JSON, MCP Registry
```

判断：不是个人身份记忆，但与“项目上下文/代码库记忆”竞争 AI 上下文预算。

### 11. mcp-memory-service

```yaml
name: mcp-memory-service
github_url: https://github.com/doobidoo/mcp-memory-service
stars: 1874
forks: 289
open_issues: 6
created: 2024-12-26
last_commit: 2026-05-23
license: Apache-2.0
language: Python
install_method: pip
transport: REST API + MCP
storage: SQLite / vector / knowledge graph
search: semantic + graph; README says autonomous consolidation
auto_capture: 强，autonomous consolidation
governance: 有 review 关键词，未见用户审批为核心
knowledge_types: knowledge graph memories
cross_tool: Claude Code, Cursor, Codex, Windsurf, Copilot, Zed, JetBrains 等
hook_mechanism: hooks present
tagline: Semantic memory layer for AI applications
target_user: AI agent pipelines and MCP clients
pricing: OSS
unique_claim: 14+ AI clients, zero cloud cost, autonomous consolidation
pypi_weekly_downloads: 9842
longmemeval_score: README 含 LongMemEval 关键词，需核验具体分数
developer_nationality_or_registry: 个人 GitHub 公开所在地 Kreuzlingen；国籍未公开
sources: GitHub, PyPI, pypistats
```

判断：直接威胁。覆盖范围和集成面广，已经在做 piia-engram 想宣传的“多客户端”心智。

### 12. MemSearch

```yaml
name: MemSearch
github_url: https://github.com/zilliztech/memsearch
stars: 1815
forks: 168
open_issues: 206
created: 2026-02-09
last_commit: 2026-05-21
license: MIT
language: Python
install_method: repo / likely Python
transport: agent integration
storage: Markdown + Milvus
search: vector semantic via Milvus
auto_capture: hooks present
governance: 未见审批机制
knowledge_types: unified memory layer
cross_tool: Claude Code, Codex explicitly in description
hook_mechanism: hooks present
tagline: A persistent, unified memory layer for all your AI agents
target_user: teams already comfortable with vector DB
pricing: OSS, backed by Zilliz ecosystem
unique_claim: Markdown + Milvus
developer_nationality_or_registry: 组织公开所在地 United States of America
sources: GitHub
```

判断：生态背书强，但依赖 Milvus，轻量个人用户门槛高于 piia-engram。

### 13. supermemory-mcp

```yaml
name: supermemory-mcp
github_url: https://github.com/supermemoryai/supermemory-mcp
stars: 1690
forks: 175
open_issues: 10
created: 2025-06-08
last_commit: 2025-12-30
license: MIT
language: TypeScript
install_method: npm supermemory-mcp
transport: MCP
storage: supermemory service
search: service-backed memory retrieval
auto_capture: 中
governance: 未见审批机制
knowledge_types: universal memories
cross_tool: “every single LLM”定位
hook_mechanism: 未见核心
tagline: Universal Memory MCP
target_user: users wanting ChatGPT memories elsewhere
pricing: no logins/paywall claim in README; service details需核验
unique_claim: Make ChatGPT memories available to every LLM
npm_weekly_downloads: 22
developer_nationality_or_registry: 组织公开所在地 United States of America
sources: GitHub, npm
```

判断：叙事与 piia-engram 接近，但更偏迁移平台内记忆，而非本地结构化身份资产。

### 14. Nocturne Memory

```yaml
name: nocturne_memory
github_url: https://github.com/Dataojitori/nocturne_memory
stars: 1135
forks: 145
open_issues: 3
created: 2025-12-25
last_commit: 2026-05-22
license: MIT
language: Python
install_method: repo
transport: MCP
storage: graph-like structured memory; SQLite/Postgres signals
search: semantic / graph
auto_capture: 中
governance: rollbackable + visual; 未见审批机制
knowledge_types: graph-like structured memory
cross_tool: Claude Code, Cursor, Codex, Windsurf
hook_mechanism: 未见核心
tagline: Rollbackable visual Long-Term Memory Server for MCP Agents
target_user: MCP agent users wanting visual/rollback memory
pricing: OSS
unique_claim: rollbackable and visual, anti-vector-RAG positioning
developer_nationality_or_registry: GitHub profile 未公开所在地；国籍未公开
sources: GitHub
```

判断：有 visual/rollback 差异点，和 piia-engram 的治理价值相邻。

### 15. claude-memory-compiler

```yaml
name: claude-memory-compiler
github_url: https://github.com/coleam00/claude-memory-compiler
stars: 1083
forks: 285
open_issues: 17
created: 2026-04-06
last_commit: 2026-04-06
license: 未在 API 返回
language: Python
install_method: repo / Claude Code hooks
transport: Claude Code hooks + Agent SDK
storage: Markdown/JSON + vector signals
search: semantic
auto_capture: 强，hooks 自动捕获 sessions
governance: LLM compiler organizes decisions/lessons，未见用户审批为核心
knowledge_types: decisions, lessons, cross-repo context
cross_tool: Claude Code first
hook_mechanism: 是，核心机制
tagline: Give Claude Code a memory that evolves with your codebase
target_user: Claude Code users
pricing: OSS/unknown
unique_claim: hooks automatically capture sessions and compiler organizes memory
developer_nationality_or_registry: 个人 GitHub profile 无所在地；国籍未公开
sources: GitHub
```

判断：证明 hooks 路线很强。piia-engram 如果只靠模型主动调用工具，会在真实体验上输给 hooks 项目。

### 16. TradeMemory Protocol

```yaml
name: TradeMemory Protocol
github_url: https://github.com/mnemox-ai/tradememory-protocol
stars: 986
forks: 126
open_issues: 1
created: 2026-02-23
last_commit: 2026-05-14
license: MIT
language: Python
install_method: repo / MCP
transport: MCP
storage: SQLite / vector signals
search: semantic
auto_capture: 中
governance: decision audit trail, SHA-256 tamper detection
knowledge_types: decisions / outcomes / trading memory
cross_tool: Claude Code, Cursor
hook_mechanism: 未见核心
tagline: Decision audit trail + persistent memory for AI trading agents
target_user: AI trading agents
pricing: OSS
unique_claim: outcome-weighted recall, tamper detection, 17 MCP tools
developer_nationality_or_registry: GitHub org 公开所在地 Taiwan
sources: GitHub
```

判断：垂直领域的“治理/审计”信号很强，说明市场可能接受 memory quality/audit 叙事。

### 17. Memory Bank MCP

```yaml
name: memory-bank-mcp
github_url: https://github.com/alioshr/memory-bank-mcp
stars: 905
forks: 87
open_issues: 15
created: 2025-02-16
last_commit: 2025-08-20
license: MIT
language: TypeScript
install_method: npm memory-bank-mcp
transport: MCP
storage: JSON / remote memory bank
search: basic memory bank retrieval
auto_capture: 中
governance: README flags include approval but需核验
knowledge_types: memory bank files
cross_tool: Claude Code, Cursor
hook_mechanism: 未见核心
tagline: MCP server implementation for remote memory bank management
target_user: Cline-style memory bank users
pricing: OSS
unique_claim: inspired by Cline Memory Bank
npm_weekly_downloads: 58
developer_nationality_or_registry: 个人 GitHub 公开所在地 Italy；国籍未公开
sources: GitHub, npm
```

判断：项目更早但近期活跃度较低；对 piia-engram 威胁中等。

### 18. mcp-knowledge-graph

```yaml
name: mcp-knowledge-graph
github_url: https://github.com/shaneholloman/mcp-knowledge-graph
stars: 862
forks: 103
open_issues: 0
created: 2024-12-09
last_commit: 2025-12-22
license: MIT
language: JavaScript
install_method: npm/repo
transport: MCP
storage: local JSON knowledge graph
search: graph traversal / entity relations
auto_capture: 低到中，agent writes memory
governance: 未见审批
knowledge_types: entities, relations, observations
cross_tool: Claude-oriented; MCP可扩展
hook_mechanism: 未见核心
tagline: Persistent memory for Claude through a local knowledge graph
target_user: Claude Desktop/Claude users
pricing: OSS
unique_claim: local knowledge graph fork
developer_nationality_or_registry: 个人 GitHub 公开所在地 Wellington, NZ；国籍未公开
sources: GitHub
```

判断：官方 memory server 知识图谱路线的代表之一，功能通用但缺少用户身份层。

### 19. Context Portal (ConPort)

```yaml
name: Context Portal / ConPort
github_url: https://github.com/GreatScottyMac/context-portal
stars: 764
forks: 80
open_issues: 23
created: 2025-05-05
last_commit: 2026-01-27
license: Apache-2.0
language: Python
install_method: repo
transport: MCP
storage: SQLite + project knowledge graph
search: RAG / graph / semantic signals
auto_capture: 中
governance: project-scoped; 未见用户审批
knowledge_types: project context, decisions, facts
cross_tool: Cursor, Windsurf 等
hook_mechanism: 未见核心
tagline: Project-specific knowledge graph for AI assistants
target_user: project memory users
pricing: OSS
unique_claim: RAG for context-aware development
developer_nationality_or_registry: GitHub profile 无所在地；国籍未公开
sources: GitHub
```

判断：在“项目上下文”上与 piia-engram 的 project snapshot 有重叠，但不是跨项目用户身份层。

### 20. Wax

```yaml
name: Wax
github_url: https://github.com/christopherkarani/Wax
stars: 738
forks: 46
open_issues: 0
created: 2026-01-20
last_commit: 2026-05-23
license: Apache-2.0
language: Swift
install_method: Swift package / CLI / agent setup
transport: MCP present
storage: single .wax file
search: on-device embeddings / FTS signals
auto_capture: 中
governance: review keyword present, not staging core
knowledge_types: documents, embeddings, structured knowledge
cross_tool: Claude Code, Cursor, Windsurf
hook_mechanism: 未见核心
tagline: Single-file memory layer for AI agents
target_user: Apple ecosystem / on-device RAG users
pricing: OSS
unique_claim: one portable file, Apple Silicon optimized, no server/API
developer_nationality_or_registry: 个人 GitHub profile 无所在地；国籍未公开
sources: GitHub
```

判断：可移植单文件方向值得关注。piia-engram 的 JSON 文件夹更透明，Wax 的单文件更像产品化资产。

### 21. Stash

```yaml
name: Stash
github_url: https://github.com/alash3al/stash
stars: 699
forks: 31
open_issues: 3
created: 2026-04-24
last_commit: 2026-05-01
license: Apache-2.0
language: Go
install_method: Docker Compose / single binary
transport: MCP over SSE
storage: Postgres + pgvector
search: vector / memory consolidation
auto_capture: 强，episodes -> facts -> relationships
governance: 未见审批
knowledge_types: episodes, facts, relationships, working context
cross_tool: Cursor, Claude Desktop, OpenCode, Windsurf, Cline, Continue 等
hook_mechanism: 未见核心
tagline: Persistent memory layer for AI agents
target_user: self-hosted agent memory users
pricing: OSS
unique_claim: Postgres/pgvector with background consolidation
developer_nationality_or_registry: 个人 GitHub 公开所在地 Hurghada, Red Sea, Egypt；国籍未公开
sources: GitHub
```

判断：架构重，适合 self-hosted 用户；不是轻量个人 identity layer。

### 22. mcp-mem0

```yaml
name: mcp-mem0
github_url: https://github.com/coleam00/mcp-mem0
stars: 677
forks: 232
open_issues: 13
created: 2025-04-13
last_commit: 2025-04-13
license: MIT
language: Python
install_method: pip package mcp-mem0 / repo
transport: MCP
storage: Mem0 + Qdrant / Postgres signals
search: semantic
auto_capture: 中
governance: Mem0 模型，无 staging 审批
knowledge_types: long-term agent memory
cross_tool: Windsurf present; MCP clients
hook_mechanism: 未见核心
tagline: MCP server for long term agent memory with Mem0
target_user: users wanting Mem0 as MCP server
pricing: OSS
unique_claim: Mem0 bridge/template
pypi_weekly_downloads: pypistats 限流未采到
developer_nationality_or_registry: 个人 GitHub profile 无所在地；国籍未公开
sources: GitHub, PyPI JSON
```

判断：桥接型项目，但会让 Mem0 更容易进入 MCP 客户端。

### 23. Vestige

```yaml
name: Vestige
github_url: https://github.com/samvallad33/vestige
stars: 538
forks: 53
open_issues: 6
created: 2026-01-25
last_commit: 2026-05-06
license: AGPL-3.0
language: Rust
install_method: single Rust binary
transport: MCP
storage: SQLite + embeddings + audit/graph signals
search: FTS + vector + FSRS + spreading activation
auto_capture: 强
governance: contradiction inspection, purge, audit; 未见 staging -> verified
knowledge_types: cognitive memory modules
cross_tool: Claude, Cursor, VS Code, Xcode, JetBrains, Codex/Windsurf keywords
hook_mechanism: hooks present
tagline: Cognitive engine that gives AI agents a brain
target_user: local cognitive memory users
pricing: OSS AGPL; Pro waitlist preview
unique_claim: FSRS-6 spaced repetition, 29 brain modules, 3D dashboard
developer_nationality_or_registry: 个人 GitHub 公开所在地 Chicago, IL；国籍未公开
sources: GitHub
```

判断：功能密度高，产品叙事强。piia-engram 不应追随其认知科学复杂度，而应反向强调简洁和用户治理。

### 24. MemoryMesh

```yaml
name: MemoryMesh
github_url: https://github.com/CheMiguel23/MemoryMesh
stars: 342
forks: 48
open_issues: 8
created: 2024-12-06
last_commit: 2026-03-01
license: MIT
language: TypeScript
install_method: npm/repo
transport: MCP
storage: knowledge graph
search: graph
auto_capture: 中
governance: 未见审批
knowledge_types: structured graph memory
cross_tool: Cursor present; MCP clients
hook_mechanism: 未见核心
tagline: Knowledge graph server using MCP
target_user: graph memory users
pricing: OSS
unique_claim: structured memory persistence for AI models
developer_nationality_or_registry: GitHub profile 无所在地；国籍未公开
sources: GitHub, PyPI JSON for memorymesh library not necessarily same repo
```

判断：通用知识图谱 memory，差异化弱于头部。

### 25. memory-graph

```yaml
name: memory-graph
github_url: https://github.com/memory-graph/memory-graph
stars: 203
forks: 71
open_issues: 2
created: 2025-11-27
last_commit: 2026-02-12
license: MIT
language: Python
install_method: repo
transport: MCP
storage: graph DB / SQLite/Postgres signals
search: graph + semantic
auto_capture: 中
governance: 未见审批
knowledge_types: coding-agent relationships
cross_tool: Claude Code, Cursor, Windsurf
hook_mechanism: hooks present
tagline: Graph DB-based MCP memory server for coding agents
target_user: coding agents needing relationship tracking
pricing: OSS
unique_claim: intelligent relationship tracking
developer_nationality_or_registry: 组织公开所在地 United States of America
sources: GitHub
```

判断：更偏 graph backend，直接威胁低于 mcp-memory-service。

### 26. ClawMem

```yaml
name: ClawMem
github_url: https://github.com/yoloshii/ClawMem
stars: 172
forks: 26
open_issues: 1
created: 2026-02-06
last_commit: 2026-05-20
license: MIT
language: TypeScript
install_method: repo
transport: Hooks + MCP
storage: SQLite + LanceDB + markdown/json signals
search: hybrid RAG, FTS, vector
auto_capture: 强，hooks
governance: review keyword present; 未见 staging -> verified
knowledge_types: on-device memory
cross_tool: Claude Code, Hermes, OpenClaw, Cursor, Codex, Windsurf
hook_mechanism: 是，核心机制
tagline: On-device memory layer for AI agents
target_user: OpenClaw/Hermes/Claude Code users
pricing: OSS
unique_claim: hooks + MCP + hybrid RAG
developer_nationality_or_registry: GitHub profile 无所在地；国籍未公开
sources: GitHub
```

判断：小但路线重要。hooks + MCP 的组合是 piia-engram 应该借鉴的体验方向。

### 27. OMEGA Memory

```yaml
name: OMEGA Memory
github_url: https://github.com/omega-memory/omega-memory
stars: 147
forks: 22
open_issues: 2
created: 2026-02-13
last_commit: 2026-05-19
license: Apache-2.0
language: Python
install_method: repo / OpenClaw plugin
transport: MCP/OpenClaw plugin
storage: local graph-based memory per project claims
search: graph/semantic; exact details需核验
auto_capture: 强
governance: 未见用户审批
knowledge_types: persistent memory
cross_tool: OpenClaw plus likely MCP clients
hook_mechanism: 未见核心
tagline: Persistent memory for local AI agents
target_user: local AI agent users
pricing: OSS
unique_claim: 官网自称 LongMemEval 95.4%, local-first
longmemeval_score: 官网自称 95.4；需独立复现
developer_nationality_or_registry: GitHub user/org 未公开所在地；国籍/注册地未公开
sources: GitHub, https://omegamax.co/
```

判断：原文把 OMEGA 作为主要对标并不荒谬，但现在它已经不是最大威胁。更大的压力来自 MemPalace、Mem0、agentmemory、Basic Memory、mcp-memory-service。

### 28. remindb

```yaml
name: remindb
github_url: https://github.com/radimsem/remindb
stars: 102
forks: 4
open_issues: 2
created: 2026-04-15
last_commit: 2026-05-23
license: MIT
language: Go
install_method: single binary / MCP
transport: MCP
storage: portable SQLite file
search: FTS / graph signals
auto_capture: 中
governance: 未见审批
knowledge_types: portable agent memory
cross_tool: Claude Code, Cursor, Codex
hook_mechanism: 未见核心
tagline: Agentic memory database, one portable SQLite file
target_user: users wanting portable local DB
pricing: OSS
unique_claim: 82-99% session token reduction, one portable SQLite file
developer_nationality_or_registry: 个人 GitHub 公开所在地 Czechia；国籍未公开
sources: GitHub
```

判断：小项目但“portable SQLite file”对 piia-engram 的可移植格式方向有参考价值。

### 29. mem0-mcp

```yaml
name: mem0-mcp
github_url: https://github.com/pinkpixel-dev/mem0-mcp
stars: 95
forks: 13
open_issues: 1
created: 2025-03-12
last_commit: 2026-05-17
license: MIT
language: JavaScript
install_method: npm/repo
transport: MCP
storage: Mem0 backend, likely vector DB
search: semantic
auto_capture: 中
governance: Mem0 model
knowledge_types: long-term memories
cross_tool: Cursor/MCP clients
hook_mechanism: hooks present
tagline: mem0 MCP Server
target_user: MCP users wanting Mem0
pricing: OSS
unique_claim: drop-in MCP server using mem0
developer_nationality_or_registry: GitHub org 公开所在地 United States of America
sources: GitHub
```

判断：同 mcp-mem0，是 Mem0 生态进入 MCP 的通道。

### 30. Remnic

```yaml
name: Remnic
github_url: https://github.com/joshuaswarren/remnic
stars: 74
forks: 11
open_issues: 8
created: 2026-02-05
last_commit: 2026-05-23
license: MIT
language: TypeScript
install_method: npm @remnic/core / @remnic/cli / @remnic/server
transport: HTTP + MCP
storage: SQLite + LanceDB/vector signals
search: semantic; retrieval quality emphasis
auto_capture: 中到强
governance: provenance, correction, boundaries, evals; 未见 verified tier
knowledge_types: scoped memory, context, traces
cross_tool: Codex CLI, Claude Code, Replit, MCP clients
hook_mechanism: hooks present
tagline: Open-source memory and context for user-aware agents
target_user: user-aware agents / OpenClaw/Hermes
pricing: OSS
unique_claim: provenance, retrieval quality, correction, boundaries, evals
npm_weekly_downloads: @remnic/core 1959, @remnic/cli 1034, @remnic/server 55
developer_nationality_or_registry: 个人 GitHub 公开所在地 Dallas, Texas；国籍未公开
sources: GitHub, npm
```

判断：虽然 stars 少，但在“user-aware agents、provenance、correction、boundaries”上靠近 piia-engram 的治理方向。

### 31. Lumetra Engram

```yaml
name: Lumetra Engram
github_url: https://github.com/lumetra-io/engram-node-red ; https://github.com/lumetra-io/engram-claude-plugin
stars: 0
forks: 0
open_issues: 0
created: 2026-05-14 to 2026-05-20
last_commit: 2026-05-20 to 2026-05-22
license: MIT
language: JavaScript / Claude plugin repo
install_method: Node-RED package / Claude plugin / SaaS
transport: API/plugin; likely MCP-adjacent
storage: cloud/SaaS memory
search: website claims BM25 + vector + knowledge graph（需持续核验）
auto_capture: 中
governance: 未见用户审批
knowledge_types: durable/explainable memory
cross_tool: Claude plugin, Node-RED
hook_mechanism: Claude plugin
tagline: Engram durable memory for Claude Code
target_user: SaaS/agent workflow users
pricing: 商业 SaaS（原任务线索 $29-99/月，需官网复核）
unique_claim: BYOK, explainable memory, LongMemEval-S claim from prior source
npm_weekly_downloads: @lumetra/node-red-contrib-engram-memory 262
developer_nationality_or_registry: GitHub org 未公开所在地/注册地
sources: GitHub, npm, https://lumetra.io/
```

判断：规模小，但“Engram”命名和商业化定位会造成 SEO/认知冲突。

### 32. Eve MCP

```yaml
name: Eve MCP
github_url: https://github.com/sherifkozman/eve-mcp
stars: 1
forks: 0
open_issues: 3
created: 2026-03-10
last_commit: 2026-04-23
license: Apache-2.0
language: Python
install_method: repo
transport: MCP
storage: 未详
search: 未详
auto_capture: 未详
governance: 未见审批
knowledge_types: persistent memory
cross_tool: README description says Claude Code, Gemini CLI, Codex CLI, any MCP client
hook_mechanism: 未见
tagline: Persistent memory for AI agents
target_user: MCP client users
pricing: OSS
unique_claim: cross-client compatibility
developer_nationality_or_registry: 个人 GitHub profile 未公开所在地；国籍未公开
sources: GitHub
```

判断：当前体量很小，作为长尾观察即可。

### 33. Official MCP server-memory

```yaml
name: @modelcontextprotocol/server-memory
github_url: https://github.com/modelcontextprotocol/servers
stars: 86132 for monorepo, not memory server alone
forks: 10794 for monorepo
open_issues: 512 for monorepo
created: 2024-11-19
last_commit: 2026-05-21
license: GitHub API NOASSERTION for monorepo; npm package MIT
language: TypeScript
install_method: npm @modelcontextprotocol/server-memory
transport: MCP stdio
storage: local knowledge graph
search: graph/entity relation
auto_capture: 低，agent manually creates entities/relations
governance: 无审批
knowledge_types: entities, relations, observations
cross_tool: any MCP client
hook_mechanism: 无
tagline: Knowledge graph memory server
target_user: general MCP users
pricing: free
unique_claim: official/reference implementation
npm_weekly_downloads: 226211
developer_nationality_or_registry: Model Context Protocol org，注册地/国籍不适用或未公开
sources: GitHub, npm
```

判断：官方 memory server 是默认基线。piia-engram 必须解释“为什么不是直接用官方 server-memory”：答案应是用户身份层、结构化个人知识、审批治理和跨工具 setup。

### 34. Letta

```yaml
name: Letta
github_url: https://github.com/letta-ai/letta
stars: 22913
forks: 2438
open_issues: 60
created: 2023-10-11
last_commit: 2026-05-14
license: Apache-2.0
language: Python
install_method: pip / Docker / cloud
transport: API; MCP wrappers/registry memory-mcp exists
storage: Postgres/self-host or Letta Cloud
search: agent memory management
auto_capture: agent self-edit memory
governance: agent-owned, not user approval first
knowledge_types: archival/recall memory for stateful agents
cross_tool: via API/integration, not personal cross-tool identity
hook_mechanism: 非核心
tagline: Platform for building stateful agents
target_user: agent developers
pricing: OSS + cloud
unique_claim: advanced memory that can learn and self-improve
pypi_weekly_downloads: pypistats 限流未采到
developer_nationality_or_registry: GitHub org 未公开所在地；官网 letta.com，无注册地证据
sources: GitHub, PyPI, MCP Registry entry com.letta/memory-mcp
```

判断：不是同型，但在“AI memory”大词上是强竞争心智。piia-engram 应持续强调不是 agent self-edit memory。

### 35. Graphiti

```yaml
name: Graphiti
github_url: https://github.com/getzep/graphiti
stars: 26425
forks: 2628
open_issues: 406
created: 2024-08-08
last_commit: 2026-05-21
license: Apache-2.0
language: Python
install_method: pip graphiti-core
transport: library/API; MCP examples/integrations
storage: temporal knowledge graph
search: graph + embeddings + semantic
auto_capture: 自动构建 real-time knowledge graph
governance: 未见用户审批
knowledge_types: entities, edges, temporal facts
cross_tool: agent framework oriented
hook_mechanism: 非核心
tagline: Build Real-Time Knowledge Graphs for AI Agents
target_user: agent developers needing temporal KG
pricing: OSS + Zep ecosystem
unique_claim: real-time temporal knowledge graphs
pypi_weekly_downloads: graphiti-core 146798
developer_nationality_or_registry: GitHub org 公开所在地 United States of America
sources: GitHub, PyPI, pypistats
```

判断：Graphiti 不是用户记忆产品，但它把“知识图谱 + temporal memory”标准拉高。

### 36. Mnemonic

```yaml
name: Mnemonic
github_url: https://github.com/danielmarbach/mnemonic
stars: 22
forks: 5
open_issues: 2
created: 2026-03-07
last_commit: 2026-05-23
license: Apache-2.0
language: TypeScript
install_method: npm @danielmarbach/mnemonic-mcp
transport: MCP
storage: plain Markdown + JSON, git sync
search: semantic
auto_capture: 中
governance: review keyword present; no verified tier
knowledge_types: project-scoped memory
cross_tool: Claude Code, Cursor, Codex
hook_mechanism: 未见核心
tagline: local MCP memory backed by markdown + JSON, synced via git
target_user: users wanting git-synced plaintext memory
pricing: OSS
npm_weekly_downloads: 877
developer_nationality_or_registry: 个人 GitHub 公开所在地 Switzerland；国籍未公开
sources: GitHub, npm
```

判断：体量小，但 plaintext+git sync 与 piia-engram 的数据可移植理念相近。

### 37. Whyline

```yaml
name: Whyline
github_url: https://github.com/malinda1986/whyline
stars: 3
forks: 0
open_issues: 0
created: 2026-05-12
last_commit: 2026-05-18
license: MIT
language: TypeScript
install_method: npm @malindar/whyline
transport: MCP
storage: SQLite/Markdown signals
search: semantic
auto_capture: hooks present
governance: 未见审批
knowledge_types: coding session rationale
cross_tool: Claude Code
hook_mechanism: 是
tagline: Git remembers what changed; Whyline remembers why
target_user: coding agents needing rationale memory
pricing: OSS
npm_weekly_downloads: 1701
developer_nationality_or_registry: GitHub profile 无所在地；国籍未公开
sources: GitHub, npm
```

判断：stars 小但 npm 下载不低。它说明“why/decision rationale”是有需求的，和 piia-engram 的 decision 类型一致。

## 对比矩阵

| 项目 | 存储 | 搜索 | 自动捕获 | 治理 | 知识类型 | Hook 机制 | 跨工具数/范围 |
|---|---|---|---|---|---|---|---|
| piia-engram | local JSON/Markdown | n-gram + alias | 中，需 AI 调用 | staging -> verified | identity, lesson, decision, playbook | 否 | 4 verified + 9 expected/fallback |
| Mem0 | vector/cloud/self-host | semantic+BM25+entity+temporal | 强 | 弱 | facts/entities | 否 | SDK/集成广 |
| MemPalace | ChromaDB/pluggable | semantic | 强 | 弱 | verbatim conversation/project | 是 | MCP, Claude Code first |
| agentmemory | SQLite/Postgres/vector/KG | hybrid | 强 | 中弱 | agent/doc/KG | 是 | Claude Code/Cursor/Codex/Windsurf |
| Gentleman Engram | SQLite+FTS5 | FTS/graph docs | 中 | 弱 | What/Why/Where/Learned | plugin/setup | 8+ agents |
| Basic Memory | Markdown + KG | semantic+graph | 中 | 中，人工编辑 | notes/entities | 否 | MCP clients |
| mcp-memory-service | SQLite/vector/KG | semantic+KG | 强 | 中弱 | KG memories | 是 | 14+ clients |
| ByteRover | portable layer | FTS/graph signals | 强 | 中弱 | coding-agent memory | 不明 | Claude/Cursor/Windsurf |
| memU | vector/KG | semantic | 强 | 弱 | memory primitives | 是 | OpenClaw/proactive agents |
| MemOS | memory OS | hybrid | 强 | 弱 | memory blocks/skills | 不明 | agent frameworks |
| MemSearch | Markdown+Milvus | vector | 强 | 弱 | unified memory | 是 | Claude Code/Codex |
| supermemory-mcp | service | service search | 中 | 弱 | universal memory | 否 | any LLM claim |
| Nocturne | graph-like | graph/semantic | 中 | rollbackable | structured graph | 否 | MCP clients |
| Official server-memory | local KG | graph | 低 | 无 | entities/relations | 否 | any MCP |
| Context Portal | SQLite project KG | RAG/graph | 中 | project-scoped | project context | 否 | IDE agents |
| claude-memory-compiler | Markdown/JSON/vector | semantic | 强 | LLM organize | decisions/lessons | 是 | Claude Code |
| ClawMem | SQLite+LanceDB | hybrid RAG | 强 | 中弱 | on-device memory | 是 | Claude/Hermes/OpenClaw/Cursor/Codex |
| Remnic | SQLite+vector | semantic | 中强 | provenance/correction | scoped memory | 是 | Codex/Claude/Replit/MCP |
| OMEGA | local graph | graph/semantic | 强 | 弱 | persistent memory | 不明 | OpenClaw/MCP |
| Wax | single .wax file | embeddings/FTS | 中 | 中弱 | docs/structured knowledge | 否 | Claude/Cursor/Windsurf |
| Vestige | SQLite+embedding+graph | FTS+vector+FSRS | 强 | contradiction/audit | cognitive modules | 是 | Claude/Cursor/VS Code/JetBrains |
| Letta | Postgres/cloud | agent memory | 强 | agent-owned | archival/recall | 否 | API/integrations |
| Graphiti | temporal KG | graph+semantic | 强 | 弱 | temporal entities/edges | 否 | frameworks |

## piia-engram 差异化评估

### a. 没有任何竞品明确做到的特性

1. **以用户身份为中心，而非 agent/task 为中心**  
   很多项目能记忆 agent 会话，但 piia-engram 明确存储“用户是谁、如何工作、质量标准、经验教训、关键决策”，并让这些跟随用户跨工具。Basic Memory、Remnic 接近，但仍更像知识库/agent context。

2. **staging -> verified 作为核心产品机制**  
   多数竞品是自动写入、自动整合；少数有 review/correction/rollback/audit，但没有看到把“AI 只能提议，用户审批后才成为 verified 知识”作为核心默认模型的同型实现。OneNomad/przm-memory README flags 出现 staging/approval，但 GitHub stars 为 0，需进一步深挖。

3. **lesson / decision / playbook 的个人知识结构**  
   claude-memory-compiler、Whyline、TradeMemory 都涉及 decision/lesson/rationale，但 piia-engram 把 lesson、decision、playbook 作为跨项目、跨工具、长期身份资产的一等类型。

4. **非 MCP 工具的身份卡 fallback**  
   `get_identity_card` 把用户上下文导出 Markdown 给 ChatGPT/Gemini/Kimi 这类非 MCP 工具，和“每个 AI 工具都能读同一身份”的定位高度一致。多数竞品只覆盖 MCP/API。

5. **本地 JSON 可直接编辑 + 个人身份资产叙事**  
   Wax 有单文件，Mnemonic 有 Markdown+JSON+git，Basic Memory 有 Markdown，但 piia-engram 的身份/偏好/质量标准/信任边界结构更贴近 PIIA。

### b. 竞品已经做到且做得更好的领域

1. **安装/分发简洁度**：Gentleman Engram、Vestige、remindb 的 single binary 叙事比 Python 包 + MCP 配置更简单。
2. **自动捕获体验**：MemPalace、claude-memory-compiler、ClawMem、mcp-memory-service 等 hooks 路线更不依赖模型主动调用。
3. **语义检索/benchmark 叙事**：Mem0、MemPalace、OMEGA、MemOS、agentmemory 都在用 LongMemEval 或 real-world benchmark 打市场。
4. **生态规模**：Mem0、Graphiti、Letta 已经拥有远超 piia-engram 的开发者生态和下载量。
5. **可视化/运维体验**：Vestige 3D dashboard、Nocturne visual rollback、Gentleman Engram TUI 都比 piia-engram 当前 CLI/HTML review 更有产品感。
6. **跨客户端宣传覆盖**：mcp-memory-service 和 Gentleman Engram 明确列出更多客户端，piia-engram 的“13 工具”在未全部实测前不宜作为硬卖点。

### c. staging -> verified 治理是否有类似实现

- 明确同型：未发现成熟竞品明确使用 staging -> verified 作为默认知识生命周期。
- 相邻机制：
  - Remnic：provenance, correction, boundaries, evals。
  - Vestige：contradiction inspection, purge, audit。
  - TradeMemory：decision audit trail, tamper detection, outcome-weighted recall。
  - OneNomad/przm-memory：README/npm 描述含 tier lifecycle、staging/approval，但体量为 0 stars，需源码级复核。
  - Registry 的 VaultCrux/Verified Repo Memory 等有 audits/contradictions/verification，但非主流。

结论：治理模型是 piia-engram 当前最真实的差异化，但需要更前置地表达，不应藏在高级功能里。

### d. 结构化知识类型是否有类似实现

- lessons/decisions：claude-memory-compiler、Whyline、TradeMemory、Context Portal 有相邻结构。
- playbook/SOP：piia-engram 的自动 playbook extraction 更少见；ByteRover/agentmemory/MemPalace 更偏会话或代码上下文，不强调用户确认后的 SOP。
- identity/preferences/quality standards/trust boundaries：未见成熟竞品以这些作为一等结构类型。

结论：单看 lesson/decision 不是唯一，但“identity + quality standards + lessons + decisions + playbooks + approval”组合仍独特。

### e. 可移植记忆格式方向

已有信号：
- ByteRover：直接使用 “portable memory layer” 叙事。
- Wax：单 `.wax` 文件，可 AirDrop/sync/backup。
- remindb：one portable SQLite file。
- Mnemonic：Markdown + JSON + git sync。
- piia-engram：JSON/Markdown + identity card + OpenClaw compat import/export。
- Knowledgine：npm 包 `@knowledgine/mcp-memory-protocol`，主张 memory protocol/spec/conformance kit，虽然项目小。

机会：把 piia-engram 的 export/import/OpenClaw compat 升级为“PIIA portable memory schema”是可行方向，但应等外部用户验证后再投入。

### f. 谁用 hooks 解决“AI 不主动调用”

明确 hooks 路线：
- MemPalace：README 首屏强调 Claude Code session expire and auto-save hooks。
- claude-memory-compiler：hooks 自动捕获 session，Agent SDK 提取决策/教训。
- ClawMem：Hooks + MCP server + hybrid RAG。
- claude-mem-lite：error-triggered recall, episode batching, FTS5。
- Vestige：README flags 含 hooks，并面向多 IDE。
- mcp-memory-service、agentmemory、MemSearch、Remnic 也出现 hooks/auto capture 关键词。

建议：piia-engram 不必立刻改成自动捕获，但至少需要 Claude Code/Codex/Cursor 的“session start / session end instruction 或 hook adapter”，否则真实激活会被 hooks 型产品拉开。

## 威胁 Top 5

### 1. MemPalace

原因：stars 52712，强 hooks，强 LongMemEval 叙事，local-first，MCP，Claude Code retention 痛点直击。它会抢走“本地 AI memory”的第一心智。

### 2. agentmemory

原因：stars 16746，定位就是 coding agents persistent memory，支持多客户端，benchmark 叙事，和 piia-engram 的开发者 ICP 高度重叠。

### 3. Gentleman-Programming/engram

原因：名字冲突最大，Go single binary，SQLite+FTS5，CLI/HTTP/MCP/TUI，跨工具 setup 覆盖广。即使功能哲学不同，也会造成搜索和用户认知混淆。

### 4. mcp-memory-service

原因：PyPI 周下载 9842，高于 piia-engram；支持 14+ 客户端，REST+MCP+knowledge graph+autonomous consolidation，直接覆盖“跨工具记忆层”。

### 5. Basic Memory

原因：stars 3071，PyPI 周下载 16651，Markdown-first + local knowledge graph 对开发者非常友好。它不是身份层，但会成为“我想让 AI 记住项目/知识”的默认选择之一。

候补威胁：ByteRover（portable coding-agent memory）、Mem0（大词心智和下载量）、Remnic（user-aware/provenance 接近治理方向）、Wax/remindb/Mnemonic（可移植格式方向）。

## 机会识别

### 1. 用户治理是最清晰空白

市场主流都在追求“自动”。这带来一个反向机会：**可信记忆不是自动记忆，而是可审计、可纠错、可批准的记忆**。piia-engram 应把“AI proposes, you approve”放到首屏。

### 2. Portable identity schema

当前没有强势标准定义“用户身份、偏好、质量标准、经验、决策、playbook 应如何跨工具携带”。piia-engram 可以先做事实标准，而不是一开始做协议组织。

建议路径：
- 先稳定 `export_engram` 和 `get_identity_card`。
- 再发布 `piia-memory-schema.json`。
- 最后做 OpenClaw/AGENTS.md/CLAUDE.md/.cursorrules adapters。

### 3. Memory quality assurance

竞品常见的是 recall benchmark，少见的是 memory quality benchmark。piia-engram 可定义：
- 过期知识检测
- 冲突检测
- 重复合并
- 来源/provenance
- 用户确认率
- 误记忆撤销率

这比硬拼 LongMemEval 更贴近治理定位。

### 4. Hooks adapter 而非全自动记忆

piia-engram 可以不走“自动写 verified memory”，但可以用 hooks 做：
- session start：强制注入/调用 `get_user_context`
- session end：生成 staging 草稿
- error/failure：建议保存 lesson
- release/部署/调试流程结束：建议 playbook

这样兼得低摩擦和用户审批。

### 5. 命名机会：PIIA 比 Engram 更独特

Engram 已经拥挤。建议对外传播从 `piia-engram` 逐步转成：
- 产品名：PIIA Engram
- 品类词：User-owned AI memory
- 技术包：piia-engram
- SEO 长尾：portable AI identity memory / cross-tool AI identity / user-governed AI memory

## 给 piia-engram 的建议

### 1. 立即调整公开定位

当前 “One memory. Every AI tool. Yours to keep.” 是好的，但仍容易落入 memory 红海。建议加第二句：

> AI can suggest memories. You decide what becomes true.

中文对应：

> AI 可以建议记忆，但只有你确认后才算数。

### 2. Phase 0 必须保留，但改名为“验证基础设施冲刺”

不要把 Phase 0 变成少提交、少发布的形式主义。完成标准应是：
- doctor 输出可脱敏粘贴
- setup report 可追踪失败步骤
- Claude Code / Cursor / Codex / Claude Desktop 四工具新环境跑通
- 每个工具有最小 instructions
- 至少 2 个外部用户完成安装观察

### 3. 优先做 hooks/instructions，不优先做新记忆能力

最大体验风险不是“没有功能”，而是“AI 不主动调用”。建议先做：
- Claude Code hook/instructions
- Codex AGENTS.md 模板
- Cursor rules 模板
- Claude Desktop prompt snippet
- `engram doctor --bundle` 诊断包

### 4. 不要硬拼 LongMemEval，但要跑 baseline

建议公开策略：
- 内部跑 LongMemEval baseline，知道自己在哪些题型弱。
- 不把分数作为主卖点。
- 做自己的治理型指标：confirmed memory rate、stale detection accuracy、duplicate merge precision、false memory rejection。

### 5. 工具数从卖点改成风险控制

60 个 MCP tools 对市场不是优势，可能意味着复杂。建议文档表达：
- 默认只有最小核心工具
- 高级工具 opt-in
- 首次体验只暴露 4-6 个动作

### 6. 不要现在做云同步/团队功能

云同步会直接进入 Mem0/Letta/Zep/Basic Memory/supermemory 的战场，也削弱 local-first 信任。应该等用户验证“个人身份资产”成立后再决定。

### 7. 把竞品页改成三层比较

建议 comparison 页面按用途分组：
- Agent memory：Mem0, Letta, Graphiti, MemPalace
- Project/repo memory：Basic Memory, Context Portal, codebase-memory-mcp
- Cross-tool personal identity：piia-engram, Gentleman Engram, mcp-memory-service, Remnic

这样比横向大表更诚实，也更容易说明“什么时候不要用 piia-engram”。

## 数据采集时间戳

- GitHub repo metadata：2026-05-24 04:10-04:45 Asia/Shanghai，通过 GitHub REST API。
- GitHub owner profile / 公开所在地：2026-05-24 04:50 Asia/Shanghai，通过 GitHub REST API。
- PyPI package metadata：2026-05-24 05:05 Asia/Shanghai，通过 PyPI JSON API。
- pypistats downloads：2026-05-24 05:00-05:10 Asia/Shanghai；部分包返回 429，因此标注为限流未采到。
- npm package metadata/downloads：2026-05-24 05:12 Asia/Shanghai，通过 npm registry 和 downloads API。
- MCP Registry memory search：2026-05-24 04:58 Asia/Shanghai，通过 `https://registry.modelcontextprotocol.io/v0/servers?search=memory&limit=100`。
- 官网/网页核验：2026-05-24 05:15-05:30 Asia/Shanghai，重点核验 OMEGA 和 Lumetra。

## 原始线索校正

- OMEGA Memory：已核验主 repo 为 `omega-memory/omega-memory`，当前 147 stars，而不是仅 plugin repo；官网自称 LongMemEval 95.4。
- Lumetra Engram：GitHub repo 体量很小，但 npm Node-RED 包有 262 周下载；作为命名/商业叙事竞争需要保留。
- Eve Memory：找到 `sherifkozman/eve-mcp`，体量 1 star，作为长尾即可。
- vexp：本轮未找到可明确归类的活跃 MCP memory 项目，暂不纳入主表。
- OMEGA 不再是最大竞品。Mem0、MemPalace、agentmemory、mcp-memory-service、Basic Memory、Gentleman Engram 对路线图的影响更大。
