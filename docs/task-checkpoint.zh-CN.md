# 可选的标签式任务检查点

[English](task-checkpoint.md)

Claude Code 的 Stop、PreCompact hook 可在入队前捕获一小段带明确标签的助手任务说明。
**默认关闭，必须明确启用；启用队列加密不等于同意捕获正文。** hook 注册方式、安装向导和客户端配置均不改变。

## 启用

在 hook 进程继承的环境中同时设置：

```sh
export ENGRAM_HOOK_TASK_CHECKPOINT_V1=1
export ENGRAM_HOOK_TRANSCRIPT_ROOTS='["/absolute/transcripts"]'
```

PowerShell 示例：

```powershell
$env:ENGRAM_HOOK_TASK_CHECKPOINT_V1 = '1'
$env:ENGRAM_HOOK_TRANSCRIPT_ROOTS = '["D:/transcripts"]'
```

请替换为自己明确允许读取的 transcript 目录。根目录白名单是包含 1–16 个本地绝对目录的
JSON 数组，不从事件、工作目录或客户端配置推断。只读取白名单内的普通 `.jsonl` 文件；
拒绝网络/设备路径、备用数据流、符号链接、junction 和越界路径。白名单缺失或无效时不捕获任务正文。
只有字面值 `1` 启用；取消设置即恢复原有捕获行为。停用不会删除或阻止处理此前已入队的快照。

## 助手必须提供的标签块

最新助手记录中必须有且仅有一个完整、连续、未引用、未置于代码围栏中的五标签块。
与前后正文用空行分隔，不能跨助手轮次拼接。英文标签区分大小写，支持半角或全角冒号。

```text
目标: 完成示例功能
约束: 保持现有示例行为
风险: 等待示例验证
上次失败原因: 示例断言失败
下一步: 运行示例验证
```

固定对应关系为 `Goal/目标 → goal`、`Constraint/约束 → constraint`、
`Risk/风险 → blocker`、`Last failure reason/上次失败原因 → last_failure_reason`、
`Next/下一步 → next_action`。中文值可直接使用。
显式未知值（`unknown`、`Unknown`、`未知`、`不详`、`未提供`、`不确定`、`N/A`、`n/a`）
存为 null 并记录省略码。单值超过 240 个 Unicode 字符时省略，不截断限定条件。
无可保留字段时不赋予任务来源权威。缺标签、重复/冲突标签、引用和代码示例均拒绝捕获；
最新助手记录不合格时不会回退到旧任务。用户、工具和 thinking 内容不能提供这些字段。

## 持久化和恢复

生产端最多读取 256 KiB 文件尾，丢弃首个可能不完整的边界记录；候选记录最多 64 KiB，
末尾记录不完整则不捕获。`task_checkpoint.v1` 对象含元数据最多 8 KiB。
规范化、组合敏感信息检测和跨字段边界检测均在持久化之前执行，准备阶段再次检查。
敏感字段存为 null 并记录 `privacy` 省略码；跨字段歧义候选整体丢弃。
过滤器不能识别所有私人叙述，因此仍需用户明确选择启用。

对象位于 `hooks/spool/` 事件的 `payload.task_checkpoint` 中，准备阶段冻结对象和规范文本。
重试不会通过重读 transcript 修补该快照。显式运行 `engram hooks drain --json` 后，
保留的五字段规范文本进入 `contexts/claude_code/<session>.md`，类型化对象进入相邻
`session_digest.v1` JSON 的 `task_checkpoint` 字段。
即使源文件在处理前被修改或删除、消息数量低于旧阈值，合法快照仍可处理。
捕获失败时，若时间允许则继续原有元数据入队；旧事件缺失源文件的重试语义保留。

**这些都是明文正文表面。** 未启用队列保护时，队列和 prepared 对象也是明文。
[队列正文保护](hook-queue-protection.md) 只覆盖队列及 prepared 正文；
下游会话 Markdown、digest JSON、发布中间文件、相关临时/备份副本及获准读取的投影仍为明文。
本功能不提供保留期、删除或安全擦除保证。

原生 resume brief 在背景节选之前直接投影目标、约束、阻塞、上次失败原因和下一步，
附带 `claude_code` 来源和现有警示：
`earlier session record; unreviewed; no approval or action authority`。
既有项目身份、revision 和新鲜度仲裁保留；更新的规范检查点仍可胜出。这是来源记录，不是客户端身份认证。

任务快照不创建知识、审核提案或批准，不更新规范项目状态，不进入 quick context 或 daily archive。
严格模式的知识准入保持不变。独立的 `hook_content_digest_v2` 偏好仍使用其自身可用的 transcript
输入，不会由本功能启用，也不从冻结的任务对象派生内容。
捕获结果与处理 receipt 分开；receipt 仅表示处理完成，不表示内容完整、已审核或已采用。
hook 不自动消费队列，直接自动接续仍需要消费者。

## 时限与 PreCompact 限制

读取、解析及延迟导入共用既有一秒应用时限，最多分配
`min(200 ms, 剩余时间 - 525 ms)`，为队列保护和诊断预留时间。
解释器启动和最终文件发布可能增加总耗时。超时线程只能在内存中完成，不能迟发文件。
加密/封装失败仍拒绝发布，不降级为明文。对宿主软失败，对任务内容封闭失败，不调用模型。

**PreCompact 仅尽力而为。** 现有异步注册可能导致宿主先改写文件、hook 后开始读取。
生产端快照关闭的是读取之后到队列消费之间的窗口，不能关闭启动前的竞争。
本功能不保证压缩前持久化，也不承诺恢复任意无标签的对话正文。
