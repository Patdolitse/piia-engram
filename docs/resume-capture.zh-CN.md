# 接续预算与捕获诊断

[English](resume-capture.md) | [中文](resume-capture.zh-CN.md)

`get_resume_brief` 优先选择当前状态、下一动作、阻塞或最近失败、关键约束，以及来源、审核和新鲜度信息，再安排背景与历史。可选的 `project_resume_pack.v1`、`agent_context_pack.v1` 使用同一套交接字段；开关仍默认关闭。直接调用 Python pack builder 时可传相同的 `token_budget`，默认 2000；没有新增 MCP 工具。

| 预算 | 普通短字段的契约 |
| --- | --- |
| 128 | 保留下一动作、阻塞/失败和来源状态，各关键字段独立限长；丢失细节必须明示。 |
| 256 | 保留下一动作、最近阻塞/失败和关键约束；可以省略背景。 |
| 512 / 1500 | 常规样本的关键字段可恢复；超长字段仍须说明截断。 |
| 默认 2000 | 使用相同优先顺序，并容纳更多辅助上下文。 |

`estimated_tokens` 是对渲染后 Markdown 的软估算（约四字符一 token），不是真实 tokenizer 测量，也不是返回数据大小的硬限制。包装、标题、省略提示和存在感首行都有成本；`budget.over_budget` 表明估算是否超过有效请求预算。为保留唯一动作或阻塞，小预算可能被超过。结构化包和响应元数据另有开销，它们的 JSON 不计入 `estimated_tokens`。不承诺任意长度的字段都能装进 128 token。128 以下预算沿用身份回退，并报告交接缺失。

缺失字段继续显示 unknown，冲突来源沿用现有 checkpoint 仲裁，不制造下一步。`omitted` 列出省略区段及字段，沿用预算原因、数量和合法知识 ID，并提供 `retrieval_hint`；两个包也复用现有遗漏元数据和取回提示。可扩大简报预算，读取 `get_project_snapshot`（含 `checkpoint_history`）、`get_recent_context`、`get_session_digest`，或使用合法知识的搜索/历史路径。指针和被截短的前缀不等于完整字段已经恢复；读取仍受相同作用域与权限限制。

原始 checkpoint 和会话摘要统一标为 **earlier session record（先前会话记录）**：未经审核的参考材料，不提供审批或行动授权。为保持兼容，现有列表仍叫 `trusted_context`，其中快照/约束项会携带该来源状态。待审知识仍是候选；合法知识保留原有 tier 和审核状态。`fresh` 描述新鲜度，`validated` 描述验证，`verified` 仍是知识层级，任何一个都不单独产生当前用户授权。strict eligibility、审批权、敏感度上限、存储 schema 和 Owner 专属 trust 细节不变。

简报和捕获诊断显示存储身份，沿用 doctor 的路径缩短规则：home 下使用相对名称，其它位置只显示末级名称。规范化存储根的稳定本地散列可以区分同名存储。当不同客户端接续结果不一致时，先比较该身份；它标识本地根，不表示账户身份或共享授权。

`engram doctor` 和 `engram doctor --json` 展示以下捕获元数据：

- pending 数量/字节和最旧年龄；
- quarantine 数量及字节（包含原因文件）；spool、quarantine 和 receipts 目录中的 partial 数量/字节；receipt 数量/字节；
- 从既有事件 envelope、隔离原因和 receipt 取得最近最多 20 项封闭结果码：`processed`、`duplicate`、`invalid-event`、`invalid-prepared-event`、`pending-cap`、`transcript-missing`、`transcript-missing-age-limit`、`transcript-missing-retry-limit`、`storage-unavailable`、`processing-failed`、`receipt-failed`、`transport-unavailable` 或 `unknown`；
- 超过七天的只读清理候选，仅汇总数量与字节。

诊断不回显摘要、转录位置、异常原文、事件 ID 或正文。没有结果元数据的旧 envelope 不显示结果；未知或不可读的结果元数据标为 `unknown`。结果只是本地证据，不证明宿主消费。无法读取本地文件时可报告 `metadata-unavailable`。数量是只读快照，并发捕获期间可能变化。

**queued** 只表示本地排队，不证明持久知识写成。**processed** receipt 表示本地处理完成，可能只生成会话记录或 staging 知识，不等于 approved。hook 输出成功不证明宿主采用上下文；没有确认时，宿主消费显示 **unknown**。

积压至少 100 条或最旧事件至少一天时，显示既有本地命令提示。先核对当前存储身份，再预览和处理：

```bash
engram hooks drain --dry-run --json
engram hooks drain --json
```

doctor、普通读取、MCP 启动及 MCP 来源提取不自动 drain，没有后台服务。诊断和 dry-run 不创建不存在的存储、不取得队列锁、不消费事件。

留存维持现有 pending 上限（1,000 条 / 16 MiB，单事件 128 KiB）。这**不是总磁盘占用上限**，quarantine、partial 和 receipt 可占额外空间；并发维护期间 pending 可暂时超限。quarantine/partial 的清理候选只供本地检查，不是删除授权；人工处置前须保留恢复证据并核对正在进行的写入。诊断不自动删除任何内容。receipt 默认保留以维持去重，在该政策中不成为清理候选。

队列正文加密、真实宿主采用试验另行处理。本次不把 corpus 加密扩展为 spool 正文保护，也不宣称已证明端到端任务收益。
