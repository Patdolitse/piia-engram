# Usage ping and telemetry

Engram sends one anonymous usage ping a day by default so we know how many installs are active.

- **What it sends:** a random install ID (created on your machine; `engram telemetry reset-id` makes a new one), the Engram version, your OS family, the Python version, the AI client name (for example Claude Code or Cursor) and the date. Nothing else.
- **What it never sends:** No lesson / decision / playbook content, no prompts, no file paths, no account or email, no command arguments, no error text. IP addresses are not stored (as with any web request, your IP is visible to the server in transit).
- **Where:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently and never affects a tool.
- **Turn it off:** `engram telemetry off` (`engram telemetry remote off` also does it), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`. It is off in CI, containers and tests, and stays off if you explicitly turned the detailed statistics or their remote sending off before 4.23.0, including a "no" in an earlier `engram setup`. From 4.23.0, setup does not ask about telemetry or change existing choices.
- **See it:** `engram telemetry status` shows whether it is on and why; `engram telemetry preview` prints the exact payload.

`engram setup` and `engram setup --advanced` show the same short notice without asking about telemetry. Detailed statistics stay off unless explicitly enabled with `engram telemetry on` (local counts and the daily ping). Remote sending is a separate opt-in (`engram telemetry remote on`), as is weekly feedback (`engram telemetry feedback on`); each needs a configured endpoint and sends counts only. Setup preserves existing choices; non-interactive setup is unchanged.

The first-value funnel (`engram telemetry funnel`) is also off by default and stays on your machine; it is never sent.

See [PRIVACY.md](../PRIVACY.md) for storage and retention details.

## 中文

Engram 默认每天发送一次匿名使用信号，用于了解活跃安装数量。

- **发送内容：** 随机安装 ID、版本、系统、Python 主次版本、AI 客户端名称、日期。安装 ID 在本机随机生成，可用 `engram telemetry reset-id` 重置。
- **不发送：** 内容、提示词、文件路径、账号或邮箱、命令参数、错误正文；不保存 IP 地址（服务器在请求传输时会看到来源 IP）。
- **查看：** `engram telemetry status` 显示状态与原因，`engram telemetry preview` 显示实际载荷。
- **关闭：** `engram telemetry off` 或 `DO_NOT_TRACK=1`；也支持 `ENGRAM_TELEMETRY=0`、`NO_TELEMETRY=1`。CI、容器和测试环境自动不发，此前明确退出的选择继续生效。
- **频率：** 每个安装最多每个 UTC 日一次，后台发送至 `https://telemetry.piia-engram.com/v1/ping`；发送失败静默跳过。

普通与高级 setup 只显示同一行说明，不询问遥测，不改变已有选择。详细统计默认关闭，需显式运行 `engram telemetry on` 开启本地计数及每日信号；远程发送（`engram telemetry remote on`）与每周反馈（`engram telemetry feedback on`）分别开启，需要配置端点且只发送计数。非交互 setup 保持不变。首次价值漏斗仅在本地记录，默认关闭。存储和保留期详见[隐私说明](../PRIVACY.md)。
