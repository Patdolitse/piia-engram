# Local review window / 本地审核窗口

## Open / 打开

Install piia-engram normally using Python with Tk support. On Windows,
double-click the installed **piia-engram-review.exe** launcher; it does not open
PowerShell or a console. You can pin that launcher to your taskbar. On other
desktop systems, start `piia-engram-review` from an application launcher or
terminal. The window does not require the optional browser Dock `[ui]` extra.

使用带 Tk 支持的 Python 正常安装 piia-engram。Windows 可双击安装生成的
**piia-engram-review.exe**，无需打开 PowerShell 或输入审批命令，也可将其固定到
任务栏。其它桌面系统可从应用启动器或终端启动 `piia-engram-review`。
该窗口不依赖网页 Dock 的可选 `[ui]` 安装包。

The launcher uses the same store selection as the local CLI (`ENGRAM_DIR` when
configured, otherwise the normal default). It never changes client configuration
or installs a service or startup task. If your Python distribution has no Tk,
use one with Tk support; the launcher will not silently install it.

启动器沿用本地 CLI 的存储选择（已配置的 `ENGRAM_DIR` 或正常默认目录）。它不修改
AI 客户端配置，不自动安装常驻服务或开机任务。Python 缺少 Tk 时，请使用支持 Tk 的
发行版；启动器不会静默安装依赖。

## Decide / 审核

Select one pending proposal. Review its complete content, type, scope, source,
risk and version. An identity proposal shows previous and proposed values;
a replacement shows the full entry it would retire. The complete proposal
section preserves structured steps, rationale, evidence, review dates and
relation fields present on the proposal.

选择一条待审提案，查看完整内容、类型、作用范围、来源、风险和版本。身份提案展示
原值与提议值，取代提案展示将被停用的旧条目及其完整内容。完整提案区保留条目中已有的
结构化步骤、保留理由、依据、复查时间和关系字段；缺少的信息不会由窗口凭空补造。

- **Approve / 批准并生效** applies only this displayed proposal and version.
- **Reject / 拒绝** uses the same rejection semantics as local CLI review.
- **Later / 稍后** leaves the proposal pending, with no approval or rejection.

Changing a proposal or its replacement target after display invalidates the
decision. Select it again and review the new content. Background refresh never
silently swaps in a new snapshot for the approval button. Opening, selecting,
refreshing, cancelling and closing do not approve anything.

提案或取代目标在展示后发生变化，本次决定会被拒绝；重新选择并查看新内容后再决定。
后台刷新不会悄悄用新版本替换批准按钮绑定的内容。打开、选择、刷新、取消及关闭均不会批准。

## Prompts / 提醒

Keep the review window running or minimized to receive prompts. New proposals
are detected from the persisted queue every five seconds. Prompts contain no
proposal body or private item identifiers; they offer **Review / 查看并审核**
and **Later / 稍后**, never an approval button. Duplicate versions are coalesced.
Turn off **Notify / 新提案弹窗提醒** for quiet operation.

保持窗口运行或最小化，程序每五秒检查持久的待审队列。提示不展示提案正文或私有条目编号，
只提供“查看并审核”和“稍后”，不在提示中提供盲批按钮。相同版本的提醒去重，多个新提案
聚合提示；取消勾选“新提案弹窗提醒”可静默运行。

When the application is closed, it does not issue prompts. Missed prompts do not
lose proposals: reopen the launcher to review the persisted pending queue. A
failed review is not reported as success and may have persisted partial results;
reload the current proposal before deciding again. Interrupted approvals use
the existing local review engine, not automatic background approval.

窗口关闭后不会弹窗，但错过提示不会丢失提案：重新启动仍能查看持久队列。审核失败不会报告
成功，但可能已持久化部分结果；重新读取当前提案后再决定。中断批准沿用现有本地审核引擎，
不自动后台批准。

## Boundaries / 边界

MCP tools gain no approval, rejection, identity-application or global-trust
permission. An agent confirmation flag or self-reported client name is not a
local UI decision. The review engine records `owner_ui` attribution in local
receipts; existing CLI review and backup formats remain available.

The local UI, like the local CLI, does not isolate the store from other programs
running as the same operating-system user. Keep desktop control and local file
access trusted. It does not claim independent human authentication.

MCP 不获得批准、拒绝、直接应用身份修改或授予全局信任的新权限。Agent 的确认参数或客户端
自报名称不等于本地界面决定。窗口以 `owner_ui` 来源记录本地回执，CLI 审核与备份格式不变。

本地界面与本地 CLI 一样，不隔离同一操作系统用户下的其它程序。请保护桌面控制与文件访问，
不要把窗口理解成独立的人类身份认证服务。
