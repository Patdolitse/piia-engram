# PIIA Engram v3.9.0 — 10 分钟 aha 体验

新用户从安装到"AI 认识我了"，10 分钟内完成。

## 新功能

### 智能扫描 + 分流导入

`engram setup` 新增 Step 4.5：自动扫描规则文件并智能分流。

**扫描范围**：
- 全局：`~/.claude/CLAUDE.md`、`~/.cursor/rules/*.mdc`
- 项目：CWD 下的 `CLAUDE.md`、`.cursorrules`、`AGENTS.md`、`.github/copilot-instructions.md`

**分流规则**（纯关键词，不依赖 LLM，< 2 秒完成）：
- 含"语言/角色/偏好/禁止/必须"→ 用户身份（写入 profile）
- 含"测试/build/deploy/数据库/路由"→ 项目规则（写入 lesson）
- 全局文件默认倾向用户身份，项目文件默认倾向项目规则

**交互流程**：
```
Step 4.5 — 智能导入

  扫描到 3 个规则文件：
  [全局] ~/.claude/CLAUDE.md (4 行有效内容)
  [项目] ./CLAUDE.md (5 行有效内容)
  [项目] ./AGENTS.md (11 行有效内容)

  分流预览：
    用户身份: 4 条
    项目规则: 16 条
    跳过:     35 条

  导入这些内容？[Y/n]:
```

### 验证引导

setup 结束时展示验证提示语：

```
  验证方法：打开你的 AI 工具，说这句话：

    请同步 Engram 上下文，然后告诉我你现在知道我什么。

  如果 AI 能说出你的角色、语言偏好、技术栈，
  就说明 Engram 已经在工作了。
```

## 验证数据

| 测试 | 结果 |
|------|------|
| 分流准确率（30 条混合内容） | 29/30 (96.7%) |
| E2E context 验证（3 项检查） | 3/3 |

## 升级

```bash
pip install --upgrade piia-engram
engram setup  # 重新运行即可体验新导入流程
```

---

**Full Changelog**: https://github.com/Patdolitse/engram/compare/v3.8.1...v3.9.0
