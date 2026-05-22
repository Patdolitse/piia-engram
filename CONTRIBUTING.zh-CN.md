# 贡献指南

感谢你考虑为 Engram 做贡献 — AI 身份层，记住你是谁，而不只是你做了什么。

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh-CN.md)

## 架构概览

```
src/engram_core/
    core.py          # 核心引擎：知识增删改查、搜索、上下文生成
    mcp_server.py    # MCP 工具/资源定义（AI 调用的接口层）
    setup_wizard.py  # 交互式安装向导
    crypto.py        # AES-256-GCM 加密（敏感画像字段）
tests/
    test_core.py     # 单元测试（核心引擎）
    test_reconcile.py# 自动同步、staging、冲突检测测试
experiments/
    benchmarks/      # 召回/注入质量基准测试（Round 10）
```

核心设计原则：
- **100% 本地** — 无云端、无遥测、无外部请求
- **用户拥有数据** — 所有知识以人类可读的 JSON 文件存储
- **MCP 原生** — 每个能力都暴露为 MCP 工具或资源
- **隐私优先** — 信任边界、静态加密、安全画像过滤

## 开发环境

```bash
git clone https://github.com/Patdolitse/engram.git
cd engram
pip install -e ".[dev]"
```

需要 Python 3.10+。可选依赖：`[secure]` 加密支持、`[remote]` SSE 传输。

## 运行测试

```bash
python -m pytest tests/ -v
```

当前基线：**437+ 测试，0 失败，78%+ 覆盖率**。所有 PR 必须维持此基线。

## 代码规范

- **Python 3.10+** — 类型注解推荐但不强制
- **改动聚焦** — 一个 PR 解决一个问题
- **可读性优先** — 三行相似代码优于过早抽象
- **测试覆盖** — 行为变更必须有对应测试
- **禁止外部调用** — 核心操作中 Engram 绝不能发起网络请求
- **双语内容** — 用户可见的字符串应同时支持中英文

## 安全准则

Engram 处理敏感个人数据，需格外小心：

- **禁止 `eval()` / `exec()` 用户数据**
- **HTML 输出必须用 `_esc()` 转义所有用户可控值**（防 XSS）
- **身份更新方法必须通过字段白名单验证** — 不得绕过
- **新的数据暴露路径必须尊重信任边界**（`restricted_fields`）
- **加密** — 敏感画像字段静态加密，新增加密字段需审核
- **安全漏洞请私下报告** — 见 [SECURITY.md](SECURITY.md)

## 提交信息规范

格式：`type: 简短描述`

类型：`feat`（新功能）、`fix`（修复）、`security`（安全）、`docs`（文档）、`test`（测试）、`chore`（杂务）、`refactor`（重构）

## 提交 PR

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 修改代码并编写测试
4. 运行全量测试 — 281+ 测试必须全部通过
5. 提交 PR，说明**改了什么**和**为什么**

## 报告问题

在 GitHub Issues 中提交，请包含：
- 操作系统和 Python 版本
- 复现步骤
- 预期行为 vs 实际行为
- Engram 版本（`pip show piia-engram`）

**安全漏洞**：请勿公开提交 Issue，发送邮件至 engram-security@proton.me。

## 许可证

贡献即表示你同意将贡献内容授权为 [Apache License 2.0](LICENSE)。

## 行为准则

友善、务实、建设性。我们的目标是让 AI 更好地认识人类，同时将记忆的控制权交还用户。
