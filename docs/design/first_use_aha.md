# 设计文档：首次使用 10 分钟 aha 体验

## 目标

新用户从 `pip install piia-engram && engram setup` 到"AI 回答出3条关于我的真实信息"，整个过程 <= 10 分钟。

**合格标准**：用户在 AI 工具中问"请同步 piia-engram 上下文，然后告诉我你现在知道我什么"，AI 回答中至少包含 3 条来自导入内容的具体信息。

## 现状分析

### 现有 `engram setup` 已做到的
- Step 1: Python 检测
- Step 2: 数据目录选择
- Step 3: 检测 Claude Code / Cursor / Claude Desktop 并写入 MCP 配置
- Step 4: 种子知识录入（角色/技术栈/语言 + 手动 lesson + CWD 下 CLAUDE.md/.cursorrules 导入）

### 缺失（导致 aha 失败的原因）
1. **只扫当前目录**：用户不一定在项目目录里运行 setup，错过 CLAUDE.md
2. **不分流**：CLAUDE.md 里"所有沟通使用中文"是用户偏好，"不用 Tailwind"是项目规则，两者混在一起存成 lesson
3. **没有验证环节**：setup 结束后用户不知道下一步该做什么
4. **没有主动扫描**：用户可能有多个项目，每个项目有 CLAUDE.md，但 setup 只看一个

## 设计方案

### 新增 Step 4.5：智能扫描 + 分流导入

在现有 Step 4（种子知识录入）之后，新增自动扫描阶段。

#### 扫描路径

```
全局文件（用户级）：
  ~/.claude/CLAUDE.md          # Claude Code 全局指令
  ~/.cursor/rules/*.mdc        # Cursor 全局规则
  ~/.claude/projects/*/CLAUDE.md  # Claude Code 项目级指令

项目文件（在 CWD 及其子目录）：
  ./CLAUDE.md
  ./.cursorrules
  ./AGENTS.md
  ./.github/copilot-instructions.md
```

限制：
- 全局文件总是扫描
- 项目文件只扫 CWD（不递归用户整个磁盘）
- 每个文件最多读取前 200 行（防止巨大文件）
- 跳过明显的 boilerplate（< 50 字符且无有效内容的文件）

#### 分流规则

每一行内容通过关键词 + 结构规则分流到两个目标：

**用户身份类**（写入 profile / preferences）：
- 含"语言/language/中文/English"→ 语言偏好
- 含"角色/role/我是"→ 角色
- 含"偏好/prefer/always/never/禁止/必须"→ 工作偏好规则
- 含"风格/style/tone"→ 沟通偏好
- 出现在全局文件（~/.claude/CLAUDE.md）中的内容默认偏向用户身份类

**项目规则类**（写入 lesson，带 project domain）：
- 含项目特定词汇（文件名、框架名、"这个 repo"）→ 项目规则
- 含"测试/test/build/deploy/CI"→ 项目工程规则
- 出现在项目级文件（./CLAUDE.md）中的内容默认偏向项目类
- 含 Git hook、pre-commit 等→ 项目类

**歧义处理**：
- 当一条规则同时匹配用户和项目关键词，优先看来源文件位置
- 全局文件（~/ 下）→ 倾向用户身份
- 项目文件（./ 下）→ 倾向项目规则

实现方式：纯关键词规则，不依赖 LLM。原因：
1. setup 阶段不应依赖外部 API
2. 速度要快（< 2 秒）
3. 分流不需要 100% 准确——粗分即可，后续用户可以通过 AI 修正

#### 交互流程

```
Step 4.5 — 智能导入

  扫描到以下规则文件：
  [全局] ~/.claude/CLAUDE.md (23 行有效内容)
  [项目] E:/myproject/CLAUDE.md (15 行有效内容)

  分流预览：
    用户身份: 5 条（语言偏好、角色、编码风格...）
    项目规则: 12 条（测试规范、框架约束...）
    跳过:     21 条（注释/格式标记/过短）

  导入这些内容？[Y/n]:
```

用户确认后一次性写入。不需要逐条确认（太慢）。

### 修改 Step 5（验证）：新增 aha 验证

在 setup 最后，替代现有的"下次打开 AI 工具，它就认识你了"文案：

```
========================================
  piia-engram 安装完成！

  身份：全栈开发者 | Python, TypeScript | 中文
  经验：已录入 3 条
  导入：17 条规则（5 条身份 + 12 条项目）

  验证方法：打开你的 AI 工具，说这句话：

    请同步 piia-engram 上下文，然后告诉我你现在知道我什么。

  如果 AI 能说出你的角色、语言偏好、技术栈，
  就说明 piia-engram 已经在工作了。
========================================
```

## 实现范围

### v1（本周交付）

1. `_scan_rule_files()` — 扫描全局 + CWD 的规则文件，返回文件列表
2. `_classify_line()` — 关键词分流单行内容，返回 "user" | "project" | "skip"
3. `_import_with_split()` — 调用 classify 后分别写入 profile/preferences 和 lesson
4. 修改 `_run_seed_knowledge_onboarding()` — 在现有逻辑后插入扫描+分流
5. 修改 setup 结尾文案 — 加入验证提示语

### 不做的

- 不扫描整个磁盘（只扫全局 + CWD）
- 不用 LLM 做分流
- 不做 UI / Web 界面
- 不修改 MCP 工具（导入走 setup，不走 MCP）
- 不自动重启 AI 工具

## 文件变更

| 文件 | 变更 |
|------|------|
| `src/piia_engram/setup_wizard.py` | 新增扫描/分流/导入函数，修改 onboarding 流程 |
| `src/piia_engram/core.py` | 可能需要新增 `update_preferences_from_rules()` 方法 |

## 验证方案

### 单元测试（不依赖 LLM）

1. `_classify_line` 测试：准备 30 条混合内容，验证分流准确率 >= 80%
2. `_scan_rule_files` 测试：用临时目录模拟文件布局，验证发现率
3. 端到端：模拟 setup 全流程，验证 get_user_context 输出含导入内容

### 人工验证

录屏跑一遍完整 setup → 打开 Claude Code → 问验证语 → 确认 AI 回答含 >= 3 条导入信息。

## 时间线

- 05-25: 本设计文档完成 + review
- 05-26: 实现 + 单元测试
- 05-27: 端到端验证 + 录屏
