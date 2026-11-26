# Usage ping and telemetry

Engram sends two small anonymous signals by default. Both are off with one command or one environment variable, and neither contains anything from your memory.

## Daily usage ping

Engram sends one anonymous usage ping a day by default so we know how many installs are active.

- **What it sends:** a random install ID (created on your machine; `engram telemetry reset-id` makes a new one), the Engram version, your OS family, the Python version, the AI client name (for example Claude Code or Cursor) and the date. Nothing else.
- **Where:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently and never affects a tool.

## Daily usage counts

Engram sends anonymous usage counts once a day by default so we can see which tools are used and where errors happen.

> **Behavior change:** these counts used to be sent only after an explicit opt-in. They are now on by default. If you opted out before, you stay opted out.

- **What it sends:** which of Engram's tools were used, each with a count bucket (`0`, `1_4`, `5_9`, `10_24`, `25_99`, `100_plus`) rather than an exact number; error categories (six fixed labels) with a count bucket; rough memory-size buckets for lessons, decisions and domains; the Engram version and whether it changed; your OS family; the Python version; the tool tier; a coarse install-age bucket; the date; and an ID that changes every day.
- **What it leaves out:** the install ID of the daily ping. The daily ID is derived from a random key that never leaves your machine. With few users, a row could still be matched to a ping from the same day by version, OS and Python version, so we do not claim the two can never be linked.
- **When:** once a UTC day has ended, the next session sends one row for that day. A day without use sends nothing. Counts wait in a `piia-engram` folder under your user config directory, never in your memory store. They are deleted after sending, after seven days, and at once when you run `engram telemetry off` or `engram telemetry remote off`; if you turn telemetry off with an environment variable instead, they are removed the next time the Engram MCP server runs.
- **Where:** `https://telemetry.piia-engram.com/v1/counts` (`ENGRAM_TELEMETRY_URL` replaces it), in the background; a failed send is skipped silently and never affects a tool.

## Both signals

- **What they never send:** No lesson / decision / playbook content, no prompts, no file paths, no account or email, no command arguments, no error text, no domain or project names. IP addresses are not stored (as with any web request, your IP is visible to the server in transit).
- **Turn them off:** `engram telemetry off` (`engram telemetry remote off` also does it), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`. They are off in CI, containers and tests, and stay off if you ever turned the statistics or their remote sending off, including a "no" in an earlier `engram setup`. `ENGRAM_TELEMETRY_REMOTE=0` turns off the counts only. After an earlier refusal of the statistics, the counts stay off until you run `engram telemetry on` or `engram telemetry remote on` with this version or a later one. Setup does not ask about telemetry or change existing choices.
- **See them:** `engram telemetry status` shows whether each is on and which setting decided it; `engram telemetry preview` prints the exact payloads.
- **Retention:** 395 days.

## Optional, off by default

- **Local statistics log:** `engram telemetry on` also writes usage counts to `~/.engram/telemetry.log`. The log stays on your machine and is never sent.
- **Weekly feedback reports:** Weekly feedback reports remain opt-in (`engram telemetry feedback on`). They have no built-in destination and are sent only when an endpoint is configured. Unlike the daily counts, a feedback report includes the labels of your most used knowledge domains; preview it with `engram feedback --dry-run`.
- **First-value funnel:** `engram telemetry funnel` is off by default and stays on your machine; it is never sent.

## Update check

The `engram` command and the MCP server ask PyPI for the latest version at most once a day (the command only in interactive terminals, the server in the background). When a newer release exists, the resume brief ends with a one-line upgrade reminder. The request carries no data about you. `ENGRAM_NO_UPDATE_CHECK=1` turns the check and the reminder off; they do not run in CI, containers or tests. `DO_NOT_TRACK` does not control this check.

See [PRIVACY.md](../PRIVACY.md) for storage and retention details.

## 中文

Engram 默认发送两种匿名信号，均可用一条命令或一个环境变量关闭，且都不包含记忆中的任何内容。

### 每日匿名使用信号

Engram 默认每天发送一次匿名使用信号，用于了解活跃安装数量。

- **发送内容：** 随机安装 ID、版本、系统、Python 主次版本、AI 客户端名称、日期。安装 ID 在本机随机生成，可用 `engram telemetry reset-id` 重置。
- **频率：** 每个安装最多每个 UTC 日一次，后台发送至 `https://telemetry.piia-engram.com/v1/ping`；发送失败静默跳过。

### 每日匿名使用计数

Engram 默认每天发送一次匿名使用计数，用于了解哪些工具被使用、错误出现在哪里。

> **行为变化：** 此前这些计数只在显式开启后发送，现在默认发送。以前退出过的选择继续有效。

- **发送内容：** 用了哪些 Engram 工具及其次数分桶（不是精确数字）、错误类别及分桶、记忆规模分桶、版本及是否变化、系统、Python 主次版本、工具层级、安装时长分桶、日期，以及一个每天更换的 ID。
- **不包含：** 每日信号的安装 ID。每日 ID 由一个只保存在本机的随机密钥派生。用户数量少时，一行计数仍可能凭版本、系统和 Python 版本与同一天的信号对上，因此我们不声称两者无法关联。
- **时间：** 一个 UTC 日结束后，下一次使用时发送该日的一行；没有使用的日子不发送。待发计数保存在用户配置目录下的 `piia-engram` 文件夹，不在记忆库中；发送后或满七天删除，运行 `engram telemetry off` 时立即删除，用环境变量关闭时在 Engram MCP server 下次运行时删除。
- **去向：** 后台发送至 `https://telemetry.piia-engram.com/v1/counts`（可用 `ENGRAM_TELEMETRY_URL` 替换）；发送失败静默跳过。

### 两种信号共同的规则

- **不发送：** 内容、提示词、文件路径、账号或邮箱、命令参数、错误正文、领域名或项目名；不保存 IP 地址（服务器在请求传输时会看到来源 IP）。
- **查看：** `engram telemetry status` 显示状态与原因，`engram telemetry preview` 显示实际载荷。
- **关闭：** `engram telemetry off` 或 `DO_NOT_TRACK=1`；也支持 `ENGRAM_TELEMETRY=0`、`NO_TELEMETRY=1`。CI、容器和测试环境自动不发，此前明确退出的选择继续生效。
- **保留期：** 395 天。

### 默认不开启的部分

普通与高级 setup 只显示同一行说明，不询问遥测，不改变已有选择；非交互 setup 保持不变。本地统计日志需运行 `engram telemetry on` 才写入，且从不发送。每周反馈报告仍需显式开启（`engram telemetry feedback on`），需要配置端点；它会包含你最常用的知识领域名称，可先用 `engram feedback --dry-run` 查看。首次价值漏斗仅在本地记录，不发送。

### 更新检查

`engram` 命令和 MCP server 每天最多向 PyPI 查询一次新版本。有新版本时，续接简报末尾会多一行升级提醒。请求不携带任何关于你的数据。`ENGRAM_NO_UPDATE_CHECK=1` 可关闭检查和提醒；CI、容器和测试环境中不检查。`DO_NOT_TRACK` 不控制这项检查。

存储和保留期详见[隐私说明](../PRIVACY.md)。
