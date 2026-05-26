# 内测招募帖草稿

## V2EX 版本（/t/create）

### 标题
[内测招募] piia-engram: 让 AI 记住你是谁，跨工具、本地存储、你说了算

### 正文

做了一个开源的 AI 记忆工具，解决一个痛点：**每次换工具或开新对话，AI 就忘了你是谁。**

简单说：你的偏好、代码标准、踩过的坑、做过的决策，存在你自己电脑上（JSON 文件），通过 MCP 让 Claude Code、Cursor、Codex 等所有工具共享同一个记忆。

和其他记忆工具最大的区别：**AI 只能建议，你确认后才算数。**

- AI 提议的知识先进 staging（待审区）
- 你确认后才变成 verified（已确认）
- 你随时可以删改、查看、导出

这和 Mem0、MemPalace 等"自动记忆"方向不同——它们是让 AI 自己记自己做了什么，piia-engram 是让你管理 AI 对你的认知。

**现在招 5-10 个内测用户**，想知道：

1. "staging → verified" 这个审批机制你觉得有用还是多余？
2. 你会主动 review staging 里的内容吗，还是根本不想管？
3. 跨工具记忆对你来说是刚需还是无所谓？

**参与方式：**

```bash
pip install piia-engram
engram setup
```

正常用几天后，跑：

```bash
engram feedback
```

把输出的 JSON 贴到这个帖子下面就行。报告只有计数和分布，不含任何个人内容。

GitHub: https://github.com/Patdolitse/piia-engram
PyPI: https://pypi.org/project/piia-engram/

需要：Python 3.10+，已装 Claude Code / Cursor / Codex 之一。

---

## 小红书版本

### 标题
AI 总忘了你是谁？试试这个开源记忆工具（招内测）

### 正文

你有没有这种体验：

- 换个 AI 工具，又要从头自我介绍
- 开个新对话，之前说的偏好全忘了
- 工具一更新，配好的习惯全没了

我做了个开源项目 piia-engram，专门解决这个问题。

核心理念：**你的 AI 记忆属于你，不属于平台。**

- 存在你自己电脑上（本地 JSON 文件）
- 所有 AI 工具共享同一份记忆
- AI 建议的东西你审批后才算数

和其他记忆工具不一样的地方：不是让 AI 自动乱记，而是让你掌控 AI 对你的认知。

现在招 5-10 个内测用户，想了解大家对"AI 记忆需要审批吗"这个问题的真实看法。

安装后正常用几天，跑一行命令就能生成匿名反馈报告（不含个人信息）。

感兴趣的评论区留言，我发安装指南。

#AI记忆 #开源 #ClaudeCode #Cursor #开发者工具

---

## 反馈收集说明（给测试用户的简要指南）

### 安装

```bash
pip install piia-engram
engram setup
```

setup 会自动检测你的 AI 工具并配置 MCP。

### 正常使用

什么都不用特别做。继续用你的 Claude Code / Cursor / Codex，AI 会自动调用 Engram 记住你的偏好和经验。

### 几天后生成反馈

```bash
engram feedback
```

报告内容：
- 知识总数（staging 多少、verified 多少）
- 确认率（你审批了多少）
- 领域分布
- 来源工具分布
- MCP 工具调用排行

**不含任何知识内容、文件路径或个人信息。**

### 分享反馈

把 JSON 贴到帖子下面，或者在对话中让 AI 调用 `export_feedback_report`。

### 我们想知道的

1. staging → verified 审批你觉得有价值吗？
2. 有没有审批但觉得多余的？
3. 跨工具记忆解决了你的真实痛点吗？
4. 装起来顺利吗？哪一步卡住了？
