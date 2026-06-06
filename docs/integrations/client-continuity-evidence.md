# Client Continuity Evidence

> Public-safe summary. This page intentionally excludes local file paths, raw
> logs, private markers, machine names, and copied-store contents.

## English Summary

Engram is a local-first identity and memory layer. The current compatibility
strategy is to keep Engram's own governed memory core while supporting external
clients through clean bridges. Hermes and OpenClaw are consumers of Engram
context; Engram is not being repositioned as a Hermes or OpenClaw plugin.

| Client / flow | Verified level | What was verified | Claim boundary |
|---|---:|---|---|
| Hermes CLI via MCP | L4 | A controlled marker stored in a copied Engram store was recovered by Hermes through the Engram MCP bridge. The baseline without Engram did not recover it. Zero-pollution checks passed. | CLI surface only. This does not verify Hermes desktop behavior or broad benchmark superiority. |
| OpenClaw-compatible static file bridge | L3 | Engram exported a verified marker into OpenClaw-compatible `MEMORY.md`; OpenClaw `oc-path` parsed it. An empty baseline had no marker. Zero-pollution checks passed. | Static snapshot only. This does not verify OpenClaw live agent/model behavior. |
| OpenClaw live agent | Not verified | A live-agent probe was attempted in an isolated profile, but provider authentication was not configured. | Do not claim live OpenClaw agent continuity yet. |

## Public Claim Language

Allowed:

- "Hermes CLI marker continuity via the Engram MCP bridge has been verified in
  a copied-store A/B run."
- "The OpenClaw-compatible static file bridge has been verified to L3 static
  snapshot A/B."
- "Raw validation logs and local machine paths remain private; public summaries
  are scrubbed and stay private."

Avoid:

- "Engram works with every AI tool."
- "OpenClaw live agent continuity is verified."
- "Full context is shared automatically."
- "Engram is a Hermes memory provider or OpenClaw plugin."

## Evidence Pack Index

Use this page as a public-safe index, not as a raw-log archive. Live validation
runs should stay in isolated local folders until scrubbed.

| Evidence class | Public-safe status | Validation boundary |
|---|---|---|
| Claude Code / Codex continuity | Public-safe summary only | Shows controlled cross-tool recall, not every MCP host. |
| Cursor / Windsurf setup | Setup-card eligible | Do not claim verified continuity until an L-level run exists. |
| Hermes CLI | L4 summary above | CLI bridge only. |
| OpenClaw static bridge | L3 summary above | Static file bridge only. |
| Raw logs / copied stores | Private | Never publish raw paths, markers, prompts, or local store contents. |

Before citing a client as "verified", use the L-level claim boundaries in
[`docs/runbooks/agent-client-validation.md`](../runbooks/agent-client-validation.md).

## 中文摘要

Engram 的方向是继续做自己的本地优先、用户可治理的记忆核心，同时为外部客户端提供兼容桥接。Hermes 和 OpenClaw 是 Engram 的外部消费端，不是 Engram 的主线，也不会把 Engram 改造成它们的插件。

| 客户端 / 流程 | 已验证级别 | 已验证内容 | 边界 |
|---|---:|---|---|
| Hermes CLI via MCP | L4 | Hermes 能通过 Engram MCP bridge 找回复制数据里的受控 marker；断开 Engram 的 baseline 找不到；零污染校验通过。 | 只验证 CLI，不代表 Hermes desktop，也不代表全面 benchmark 胜出。 |
| OpenClaw 静态文件桥 | L3 | Engram 导出的 `MEMORY.md` 能被 OpenClaw `oc-path` 解析到；空 baseline 没有 marker；零污染校验通过。 | 只验证静态快照桥，不代表 OpenClaw live agent / live model 行为。 |
| OpenClaw live agent | 未验证 | 已尝试 isolated profile 探针，但 provider auth 未配置。 | 暂时不能声称 OpenClaw live agent 连续性已通过。 |

## 公开材料使用方式

这份文档可以作为公开说明或 release note 的依据。不要上传本机完整报告、raw logs、真实磁盘路径或真实 Engram 数据。需要公开时，只引用上面的级别、方法和边界。
