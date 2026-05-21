# Engram v3.6.0 — 身份层定位 + 冷启动种子知识 + 跨工具决策记忆

完全向后兼容。已有用户无需任何操作。

## 新功能

**1. 冷启动种子知识录入** — `engram setup` 新增引导填写角色、技术栈、3 条踩坑经验，自动检测并可选导入 `CLAUDE.md` / `.cursorrules`。新用户装完不再是空 Engram。

**2. 决策进入冷启动上下文（P1 bug 修复）** — `get_user_context` 和 `export_identity_card` 之前只读 lessons、跳过 decisions，AI 重复问已定过的事。现在自动包含最近 6-8 条 active decision。回归测试：决策提及率 0/10 → 10/10。

**3. `engram stats`** — 一键查看知识资产 30 天/全量增长。`engram stats --days 7` 自定义窗口。

**4. 工具分层（opt-in）** — `ENGRAM_TOOLS=core` 暴露 10 个核心工具，默认 `all` 保留全部 37 个。不影响现有用户。

## 定位刷新

README 重写明确：Engram 不是 agent memory（不是 Mem0/Zep/Letta），是**跨工具、本地优先的 AI 身份层**。Claude Code / Codex / Cursor 共享同一份你是谁。ICP 收窄到多工具开发者。

## 研究档案：协调器方向已否决

`experiments/` 新增 4 轮基准测试，验证"5 高级工具替换 37 原子工具"方案。真实 DeepSeek LLM 评估：

- 37 工具在真实 AI 下准确率 **90%**，够用
- LLM 真懂语义，不需要协调器"简化选择"（F 测试，关键词替换准确率反而 +13.3pp）
- 协调器内部 domain 推断仅 **54.3%**，反而引入新错误

**决定：协调器不合入主代码**，代码留在 `experiments/coordinator/` 作为研究档案。

这次测试验证了"先验证再上线"方法论——几天测试避免了几周方向错误。

## 升级

```bash
pip install --upgrade piia-engram
```

无需重启 MCP，下次工具冷启动自动生效。

---

**Full Changelog**: https://github.com/Patdolitse/engram/compare/v3.5.1...v3.6.0

🤖 本版本基于 Claude Code + Codex 协作开发，4 轮真实 LLM 基准测试支撑。
