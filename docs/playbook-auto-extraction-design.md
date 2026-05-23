# Playbook 自动提取方案

> **状态**：已实现（含 P0 改进：检查点优先、脱敏、禁止自动晋升、置信度分级、开关控制）  
> **目标**：让 AI 工具在完成多步骤操作后，自动生成可复用的操作手册（Playbook）草稿  
> **核心原则**：Engram 起草，用户确认，AI 完成

---

## 1. 要解决的问题

用户和 AI 工具协作时，经常会完成多步骤的操作流程（如发布版本、部署服务、上架应用）。这些流程：

- **每次都要重新摸索** — AI 工具不记得上次怎么做的
- **过程中踩的坑会丢失** — 下次还会踩同样的坑
- **用户不会主动记录** — 太麻烦，流程一完成就去做下一件事了

Engram 已有 Playbook 存储功能（`add_playbook`），但完全依赖手动触发。AI 工具不知道什么时候该调用，用户也不知道这个功能存在。

## 2. 方案概览

在 `wrap_up_session`（会话收尾）时，自动分析会话摘要，检测是否包含可复用的操作流程。如果检测到，自动提取步骤、注意事项，生成 Playbook 草稿存入暂存区，并通知用户。

```
AI 执行了一系列操作
      ↓
会话结束，AI 调用 wrap_up_session(summary=...)
      ↓
Engram 分析 summary → 检测到多步骤流程
      ↓
自动提取：步骤 + 注意事项 + 关键词
      ↓
生成 Playbook 草稿 → 存入 staging
      ↓
wrap_up_session 返回值带 playbook_draft 通知
      ↓
AI 工具展示给用户：
  "本次完成了一个操作流程，已生成草稿《XXX》（5步+2注意事项）。
   需要查看或完善吗？"
      ↓
用户选择：
  ├─ "看看" → AI 调 get_playbook 展示
  ├─ "补充 XX" → AI 调 update_playbook 完善
  ├─ "没问题" → 确认，晋升为正式知识
  └─  忽略 → 留在 staging，不影响任何东西
```

## 3. 检测逻辑：这是不是一个流程？

从 summary 文本中扫描三类信号：

### 信号 A：顺序标记
表示步骤有先后顺序的词语：
- 中文：先、然后、接着、最后、之后、随后、完成后、接下来
- 英文：first, then, next, finally, after that, subsequently
- 格式：step 1, step 2, step 3

### 信号 B：操作动词
表示实际执行了某个操作（不是讨论或分析）：
- 中文：安装、配置、部署、发布、执行、运行、创建、更新、下载、编译、构建、推送、上传、提交、打包、上架
- 英文：install, configure, deploy, publish, execute, run, create, update, download, build, push, upload, commit, package, release

### 信号 C：流程关键词（PLAYBOOK_TRIGGERS）
直接表示流程性质的词语：
- 中文：流程、步骤、怎么做、操作、发布、部署、上架
- 英文：playbook, procedure, how to, steps, workflow, runbook

### 判定规则（含置信度分级）

检测分两层，**检查点优先**：

```
层 1：检查点信号（最强）
  save_agent_context 检查点 ≥ 3  →  confidence = "high"
  （直接跳过文本扫描）

层 2：文本信号（兜底）
  (顺序标记 ≥ 2 且 操作动词 ≥ 3)  →  confidence = "medium"
  或
  (流程关键词 ≥ 3 且 非触发操作动词 ≥ 1)  →  confidence = "medium"
```

**关键改进（P0）：**
- **检查点优先**：3+ 个检查点是最可靠的流程信号，直接判定为高置信度，无需文本分析
- **非触发操作动词过滤**：规则 2（关键词路径）要求操作动词不在 PLAYBOOK_TRIGGERS 中，防止"发布"等词同时命中关键词和动词导致误判
- **词边界匹配**：英文操作动词使用 `\b` 正则边界，防止 "run" 在 "runbook" 中误匹配；中文保持子串匹配

设计倾向：**宽松判定，宁可多提取**。因为提取结果进入 staging 暂存区，不是正式知识，后续有机会审核。漏掉一个有价值的流程比多存一个无用草稿的损失更大。

## 4. 提取逻辑：怎么拆出步骤？

### 数据来源（按优先级）

| 优先级 | 数据源 | 特点 |
|--------|--------|------|
| 1 | `save_agent_context` 检查点 | 实时采集，每个里程碑一条，"已完成：XXX" |
| 2 | summary 中的数字列表 | "1. XXX  2. XXX  3. XXX" |
| 3 | summary 中的顺序标记切分 | "先 XXX，然后 XXX，最后 XXX" |

### 优先级 1：从 context 检查点提取

Engram 的 `save_agent_context` 功能在会话过程中自动保存检查点，格式为：

```markdown
### 14:15
已完成：版本号三处同步更新
### 14:30
已完成：commit+push 并等 CI 通过
### 14:45
已完成：PyPI 发布成功
```

每个"已完成"项天然就是一个步骤，且有时间顺序。这是最准确的步骤来源。

**前提**：AI 工具在会话过程中确实调用了 `save_agent_context`。如果没有检查点（数量 < 3），fallback 到优先级 2。

### 优先级 2：从数字列表提取

正则匹配 `\d+[.、）)] 内容`，每个匹配项作为一个步骤。

### 优先级 3：从顺序标记切分

按"先/然后/接着/最后"等标记切分文本，每段过滤掉非操作性内容后作为一个步骤。

### 最低要求

提取到的步骤数 **≥ 3** 才生成 Playbook。不足 3 步的不算操作流程。

## 5. 附加提取

### 注意事项（pitfalls）

扫描 summary 中的负面模式句子：
- 中文标记：踩坑、报错、失败、不行、不能、注意、小心、陷阱
- 英文标记：error, failed, rejected, gotcha, caveat, workaround

每个命中的句子提取为一条 pitfall，上限 5 条。

### 触发关键词（triggers）

从 summary 中提取 3-8 个关键词，用于未来搜索命中：
- 匹配到的 PLAYBOOK_TRIGGERS
- 推断出的技术领域（python, mcp, docker 等）

### 标题

从 summary 第一行/第一句提取，去掉"完成"等前缀，截断到 60 字符。

## 6. 存储与通知

### 敏感信息脱敏（P0 新增）

在存储之前，自动对步骤内容和注意事项执行脱敏，替换为 `[REDACTED]`：

| 类型 | 匹配模式 | 示例 |
|------|---------|------|
| Bearer Token | `Bearer\s+\S+` | `Bearer eyJ...` → `[REDACTED]` |
| API Key (sk-) | `sk-[A-Za-z0-9]{20,}` | `sk-9d970b7bc...` → `[REDACTED]` |
| GitHub Token | `ghp_[A-Za-z0-9]{36,}` | `ghp_abc123...` → `[REDACTED]` |
| Windows 绝对路径 | `[A-Z]:\\[\w\\.-]+` | `C:\Users\me\secret` → `[REDACTED]` |
| Unix 绝对路径 | `/(?:home\|Users\|tmp\|var)/[\w/.-]+` | `/home/user/.env` → `[REDACTED]` |
| 邮箱地址 | `\S+@\S+\.\S+` | `me@example.com` → `[REDACTED]` |
| 环境变量密钥 | `\w+_(?:KEY\|SECRET\|TOKEN\|PASSWORD)=\S+` | `AWS_SECRET=xxx` → `[REDACTED]` |

### 存入 staging

自动提取的 Playbook 以 `tier: "staging"` 存入，不是正式知识。staging 机制是安全网：
- 不会污染正式知识库
- 用户确认后可晋升为 verified
- **禁止自动晋升**（P0 改进）：Playbook 不参与基于访问次数的自动晋升。原因：流程性知识如果有误会导致操作失败，晋升必须经过用户确认或成功复用验证

### wrap_up_session 返回值

```json
{
  "insights": { "saved_lessons": 2, "saved_decisions": 1 },
  "playbook_draft": {
    "title": "Engram 版本发布流程",
    "playbook_id": "a1b2c3d4e5f6",
    "steps_count": 5,
    "pitfalls_count": 2,
    "tier": "staging",
    "confidence": "high",
    "message": "检测到可复用的操作流程，已生成 Playbook 草稿。"
  }
}
```

**置信度影响通知行为（P0 新增）：**
- `confidence: "high"` → AI 主动通知用户："检测到可复用的操作流程，已生成 Playbook 草稿。"
- `confidence: "medium"` → AI 静默存入草稿："检测到可能的操作流程，已静默存入草稿。"

AI 工具看到 `playbook_draft` 字段后，根据 confidence 决定通知力度。Engram 只提供结构化数据。

## 7. 用户交互模型

用户**不需要学习任何新命令**。整个流程对用户来说是：

1. 正常和 AI 协作完成操作
2. 会话结束时看到："我帮你记了个操作手册草稿，要看看吗？"
3. 用自然语言说"看看"/"补充一下 XX"/"没问题"
4. AI 翻译成 `get_playbook` / `update_playbook` / `review_knowledge` 调用

下次执行类似操作时：
1. 用户说"发布新版本"
2. AI 调 `search_knowledge("发布")` → 命中已有 Playbook
3. AI 按步骤执行，跳过已知陷阱

## 8. 参考项目

| 项目 | 启发 |
|------|------|
| AgentRR（Record & Replay） | 三阶段：记录轨迹 → 摘要为经验 → 未来重放 |
| Strands Agent SOP（AWS） | SOP 格式设计：参数化步骤 + MUST/SHOULD/MAY 约束 |
| Hindsight MCP | "reflect" 操作：从原始记忆中合成更高层次的模式 |
| ACL 2025 Conversation→Workflow | 从对话日志中提取结构化工作流 |

## 9. 开关控制（P0 新增）

用户可以随时关闭或重新开启 Playbook 自动提取：

```
用户说"关闭 playbook"/"不要自动记录流程"/"停止 playbook"
  → AI 调 update_identity(field="preferences", updates_json='{"playbook_auto_extract": false}')

用户说"开启 playbook"/"恢复自动记录"/"启动 playbook"
  → AI 调 update_identity(field="preferences", updates_json='{"playbook_auto_extract": true}')
```

**实现细节：**
- 偏好字段：`preferences.playbook_auto_extract`（布尔值，默认 `true`）
- 判定方式：`prefs.get("playbook_auto_extract") is False` — 精确匹配 `False`，字段不存在视为开启
- 关闭后 `wrap_up_session` 仍正常提取 lessons/decisions，仅跳过 Playbook 检测
- 手动 `add_playbook` 不受开关影响

## 10. 当前实现的局限

1. **纯规则引擎** — 不用 LLM，所以对非标准表述的识别有限（如"搞定了 A，又搞了 B，最后弄了 C"可能漏掉）
2. **依赖 summary 质量** — AI 写的 summary 越详细，提取效果越好；如果 summary 只有一句话，基本不会触发
3. **检查点覆盖率** — 依赖 AI 工具在过程中调用 `save_agent_context`，目前执行率不确定
4. **步骤粒度** — 自动提取的步骤可能过粗或过细，需要用户/AI 后续完善
5. **跨会话流程** — 如果一个流程跨越多次会话（今天做了前两步，明天做后三步），当前方案无法合并

## 11. 未来可能的改进

- **LLM 辅助提取**：用 DeepSeek/本地模型分析 summary，提高提取精度
- **会话轨迹记录**：不只是 context 检查点，记录实际执行的命令/工具调用序列
- **Playbook 模板化**：支持参数（如版本号、项目名），让 Playbook 可参数化重放
- **自动重放**：用户说"按上次的流程来"，AI 自动加载 Playbook 逐步执行
- **跨会话合并**：检测到部分匹配的流程时，合并为完整 Playbook

---

> 欢迎提出建议：检测规则是否合理？步骤提取策略有没有遗漏？用户交互模型是否自然？
