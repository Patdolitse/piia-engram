# Engram v3.5.1 — MCP 迁移保护 + 配置健康检查

## 升级说明

**从旧版升级？** 如果你之前用的是 `piia-pkc` 命名的 MCP server，本版本会自动帮你迁移，无需手动操作。如果 MCP 显示断开，运行 `engram doctor --fix` 一键修复。

---

## 新功能

### `engram doctor` — 配置健康检查

```bash
engram doctor          # 检查所有 AI 工具的 MCP 配置
engram doctor --fix    # 自动修复发现的问题
```

扫描 Claude Code、Cursor、Claude Desktop 的配置文件，检测：
- 旧版 server 名称（`piia-pkc`、`piia_pkc`、`piia-pkc-mcp`）
- Python 路径失效
- `mcp_server.py` 路径失效

### 自动迁移（静默，零用户操作）

MCP server 启动时自动检测并清理旧版配置，每个版本只运行一次。迁移日志写入 `~/.engram/migration.log`，不影响 MCP 协议通信。

### `engram setup` 增加反馈链接

安装完成后显示 GitHub Issues 链接，方便用户反馈问题。

---

## 背景

v3.5.0 将 MCP server 从 `piia-pkc` 更名为 `engram`，但已有用户的配置文件仍指向旧名称，导致 MCP 显示"disconnected"。本版本通过两层机制彻底解决这个问题：

1. **被动层**：`auto_migrate()` — server 启动时自动处理，用户无感知
2. **主动层**：`engram doctor --fix` — 用于 auto_migrate 覆盖不到的边缘情况

---

## 文档更新

- README（中英双语）新增 **Upgrading** 章节
- 新增 FAQ：升级后 MCP 断开怎么办

---

## 完整更新日志

- `feat`: `engram doctor` 和 `engram doctor --fix` 命令
- `feat`: `auto_migrate()` — 启动时静默迁移旧配置，哨兵文件防重复
- `feat`: `_write_mcp_config` 自动清理旧版 server 名称
- `feat`: `engram setup` 完成页面增加 Issues 反馈链接
- `docs`: README / README.zh-CN 新增 Upgrading 章节和 FAQ

**完整对比**：[v3.5.0...v3.5.1](https://github.com/Patdolitse/engram/compare/v3.5.0...v3.5.1)
