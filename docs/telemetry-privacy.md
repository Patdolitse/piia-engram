# Usage ping and telemetry

Engram sends one anonymous usage ping a day so we know how many installs are active.

- **What it sends:** a random install ID (created on your machine; `engram telemetry reset-id` makes a new one), the Engram version, your OS family, the Python version, the AI client name (for example Claude Code or Cursor) and the date. Nothing else.
- **What it never sends:** No lesson / decision / playbook content, no file paths, no account or email, no command arguments, no error text. The server does not store IP addresses (as with any web request, your IP is visible to it in transit).
- **Where:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently and never affects a tool.
- **Turn it off:** `engram telemetry off` (`engram telemetry remote off` also does it), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`. It is off in CI and in containers, and stays off if you explicitly turned the detailed statistics or their remote sending off before 4.23.0, including a "no" in an earlier `engram setup`. From 4.23.0, declining detailed statistics in setup does not turn off the daily ping; setup also preserves earlier opt-outs.
- **See it:** `engram telemetry status` shows whether it is on and why; `engram telemetry preview` prints the exact payload.

Detailed usage statistics and the weekly feedback report stay off until consent. `engram setup` and `engram setup --advanced` ask the same single statistics question, default Yes: Yes (including accepting the default) enables local logging, remote statistics and weekly feedback together; No disables them without changing the daily ping preference. Non-interactive setup leaves these choices unchanged. CLI controls remain separate (`engram telemetry on`, `engram telemetry remote on`, `engram telemetry feedback on`). Remote sending is a separate opt-in from the daily ping, sharing the setup statistics question; it requires a configured endpoint, and these reports send counts only.

The first-value funnel (`engram telemetry funnel`) is also off by default and stays on your machine; it is never sent.
