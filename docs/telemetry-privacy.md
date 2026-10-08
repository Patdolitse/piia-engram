# Usage ping and telemetry

Engram sends one anonymous usage ping a day so we know how many installs are active.

- **What it sends:** a random install ID (created on your machine; `engram telemetry reset-id` makes a new one), the Engram version, your OS family, the Python version, the AI client name (for example Claude Code or Cursor) and the date. Nothing else.
- **What it never sends:** No lesson / decision / playbook content, no file paths, no account or email, no command arguments, no error text. The server does not store IP addresses (as with any web request, your IP is visible to it in transit).
- **Where:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently and never affects a tool.
- **Turn it off:** `engram telemetry off` (`engram telemetry remote off` also does it), `ENGRAM_TELEMETRY=0` or `DO_NOT_TRACK=1`. It is off in CI and in containers, and stays off if you turned the detailed statistics or their remote sending off before, including a "no" in `engram setup`.
- **See it:** `engram telemetry status` shows whether it is on and why; `engram telemetry preview` prints the exact payload.

Detailed usage statistics and the weekly feedback report are separate and stay off unless you turn them on (`engram telemetry on`, `engram telemetry remote on`, `engram telemetry feedback on`). Remote sending is a separate opt-in, and they send counts only.

The first-value funnel (`engram telemetry funnel`) is also off by default and stays on your machine; it is never sent.
