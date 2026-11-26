# Privacy & Data Practices

piia-engram is a **local-first** tool. Your identity, preferences, lessons, and decisions are stored as plain JSON files on your machine. This document describes exactly what data piia-engram handles and how.

## Data Flow Overview

```
┌─────────────────────────────────────────────────────────────┐
│  YOUR MACHINE (~/.engram/)                                  │
│                                                             │
│  identity.json ─┐                                          │
│  lessons.json   ├── Local JSON files (you own these)       │
│  decisions.json ─┘                                          │
│                                                             │
│  ┌──────────────┐    MCP (local stdio)    ┌──────────────┐ │
│  │ Claude Code  │◄──────────────────────►│  piia-engram  │ │
│  │ Cursor       │   (no network)         │  MCP server   │ │
│  │ Codex        │                        └──────┬───────┘ │
│  └──────────────┘                               │          │
│                                                  ▼          │
│                                        telemetry.log       │
│                                        (local, opt-in)     │
└─────────────────────────────────────────────────────────────┘
```

Default implementation: your data stays in local files. Engram sends one anonymous usage ping a day by default (see [Daily usage ping](#daily-usage-ping-on-by-default)) and anonymous usage counts once a day by default (see [Daily usage counts](#daily-usage-counts-on-by-default)); turn both off with `engram telemetry off` or `DO_NOT_TRACK=1`. Both interactive setup paths show the same notice without asking about telemetry or changing existing choices. The local statistics log stays off unless you turn it on with `engram telemetry on`, and it is never sent. Weekly feedback reports are opt-in (`engram telemetry feedback on`) and are sent only when an endpoint is configured.

## What piia-engram stores locally

| Data | Location | Purpose |
|------|----------|---------|
| Your profile (name, role, preferences) | `~/.engram/identity/profile.json` | AI tools know who you are |
| Lessons learned | `~/.engram/knowledge/lessons.json` | AI tools remember your experience |
| Key decisions | `~/.engram/knowledge/decisions.json` | AI tools understand your reasoning |
| Lessons and decisions moved out of the active files (storage limits, entries an overwrite import replaced, earlier versions of edited entries) | `~/.engram/knowledge/overflow_archive/{lessons,decisions}.jsonl` | Nothing is dropped; append-only, encrypted like the active files when encryption is on, not pruned automatically, included in `export_all` (earlier versions excepted); `engram retention plan` shows what is there |
| Playbooks | `~/.engram/playbooks/{id}.json` + `~/.engram/playbooks/_index.json` | Reusable multi-step procedures |
| Project snapshots | `~/.engram/projects/` | Per-project context |
| Session history | `~/.engram/contexts/{tool}/` | Cross-session continuity |

All files are plain JSON (the overflow archive is JSON Lines). You can open, edit, back up, or delete them at any time.

## Session-end content digest (opt-in, default off)

The Claude Code session-end hook can feed a sanitized digest of the
conversation's assistant text into local knowledge extraction. This is
**off by default** and only activates when the `hook_content_digest_v2`
preference is set to the literal boolean `true` (via
`update_preferences`/`update_identity`; strings, numbers, and the legacy
`hook_content_digest` boolean key are all ignored — a persisted `true`
from an older version is inert by construction).

When enabled:

- only assistant text blocks are read from the local transcript — user
  messages and tool input/output are never collected;
- text is filtered (code fences, quotes, XML envelopes dropped), normalized
  (NFKC + zero-width folding BEFORE detection), and scrubbed of
  credential/path/PII shapes, under hard size budgets;
- cross-line secret pairs (key-form line + value line) are detected after
  normalization, so zero-width or homoglyph obfuscation of the key form
  cannot bypass the pairing;
- the final whole-digest rescan never returns the raw digest when any
  redaction fired;
- extracted items are staged for your review, never auto-verified;
- the audit trail records category + counts only (no item text);
- every candidate is checked by an output guard (including a
  prev+current sentence window for cross-line pairs) before it is
  stored; anything secret-shaped is dropped, not stored.

Residual risks you accept when opting in: the filters are shape-based,
not semantic — names, business secrets, or unusual secret formats without
a recognizable shape can pass into staged items; natural-language
paraphrases of a secret cannot be caught by finite regular expressions
and are an explicitly accepted residual risk; staged items are included
in full local backups/exports you create; and the output guard is
deliberately over-broad, so legitimate content hashes or checksums in a
session may cause some candidate items to be dropped. Review staged items
with `engram review` and delete anything unwanted.

## Network requests

### Default identity and knowledge tools: zero network requests

With default settings, identity, knowledge, search, review, and governance tools operate on local files. They make **no network requests** of their own — no API calls, no analytics. Separately, Engram sends the daily usage ping and the daily usage counts described below, and checks for a newer release as described under [Update check](#update-check).

Apart from those, the only exception is the optional `read_web_content` tool, which fetches a URL you explicitly provide — either through a local sidecar (if you run one) or the self-contained built-in reader. Both paths fetch only the URL you pass in; no other data leaves your machine.

### Daily usage ping (on by default)

Engram sends one anonymous usage ping a day by default so the project knows how many installs are active. It contains a random install ID (created on your machine, stored in a `piia-engram` folder under your user config directory; `engram telemetry reset-id` makes a new one), the Engram version, OS family, Python major.minor version, the AI client name and the date. No content, prompts or file paths are sent. It never contains account details, command arguments or error text; IP addresses are not stored (the server sees the source IP in transit); pings are kept for 395 days.

- **Off when:** `engram telemetry off` (or `engram telemetry remote off`), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`; in CI, containers and tests; or if you explicitly turned the statistics or their remote sending off before 4.23.0 (including a "no" in an earlier `engram setup`). From 4.23.0, setup does not ask about telemetry or change either preference, including earlier opt-outs. `engram telemetry on` turns it back on (together with the daily usage counts and the local statistics log).
- **Transparent:** `engram telemetry status` shows whether it is on and why; `engram telemetry preview` prints the exact payload.
- **Endpoint:** `https://telemetry.piia-engram.com/v1/ping`, at most once per UTC day, in the background; a failed send is skipped silently.

### 每日匿名使用信号（中文）

Engram 默认每天发送一次匿名使用信号：随机安装 ID、版本、系统、Python 主次版本、AI 客户端名称、日期。不发送内容、提示词或文件路径；不保存 IP 地址（请求传输时服务器会看到来源 IP）。信号最多每个 UTC 日一次，保留 395 天。

查看：`engram telemetry preview`；关闭：`engram telemetry off` 或 `DO_NOT_TRACK=1`，也支持 `ENGRAM_TELEMETRY=0`、`NO_TELEMETRY=1`。CI、容器和测试环境自动不发，此前明确退出的选择继续生效。

普通与高级 setup 只显示同一行说明，不询问遥测，也不改变已有选择；非交互 setup 保持不变。每日使用计数见下文。更多信息见[遥测说明](docs/telemetry-privacy.md)。

### Update check

The `engram` command checks PyPI for a newer version at most once a day, only in interactive terminals and never in CI; `engram doctor` also checks on each run. The MCP server makes the same check in the background, at most once a day and never while it starts, so that people who only use an AI client hear about new releases too: when a newer release exists, the resume brief ends with one fixed line naming the new and the installed version and the upgrade command. The brief itself and the session-start hooks only read the stored answer and make no request. The request is a plain download of the public package information and carries no data about you. `ENGRAM_NO_UPDATE_CHECK=1` turns the check and the reminder off; neither runs in CI, containers or tests.

更新检查（中文）：`engram` 命令与 MCP server 每天最多向 PyPI 查询一次新版本（server 在后台查询，不在启动时）。有新版本时，续接简报末尾会多一行固定格式的升级提醒。请求不携带任何关于你的数据。`ENGRAM_NO_UPDATE_CHECK=1` 可关闭检查和提醒；CI、容器和测试环境中不检查。

### Daily usage counts (on by default)

Engram sends anonymous usage counts once a day by default so the project can see which tools are used and where errors happen. Once a UTC day has ended, the next session sends one row for that day. A day without use sends nothing.

> **Behavior change:** before this release these counts were sent only after an explicit opt-in. They are now on by default. Everyone who opted out before stays opted out.

| Field | Example | What it is |
|-------|---------|------------|
| Tools used | `{"add_lesson": {"ok": "5_9", "err": "1_4"}}` | Names of Engram's own tools, each with a count bucket (`0`, `1_4`, `5_9`, `10_24`, `25_99`, `100_plus`) — never an exact number, never arguments |
| Error categories | `{"timeout": "1_4"}` | Six fixed categories with a count bucket — never error text or stack traces |
| Memory size | `{"lessons": "10_49", "decisions": "1_9", "domains": "1_9"}` | Size buckets (`0`, `1_9`, `10_49`, `50_199`, `200_999`, `1000_plus`) — never exact totals, never names |
| Engram version | `"4.30.0"` | Version number |
| Version change | `"first"`, `"same"`, `"upgrade"`, `"downgrade"`, `"changed"` | Compared with the last day sent |
| Install-age bucket | `"first_day"`, `"2_7_days"`, `"8_30_days"`, `"31_plus_days"` | Coarse bucket, not the install time |
| OS family | `"windows"`, `"macos"`, `"linux"`, `"other"` | No version |
| Python version | `"3.12"` | Major.minor only |
| Tool tier | `"core"`, `"all"`, `"other"` | Which tool set is loaded |
| Daily ID | `"a3f8b2c1e9d04f67"` | Changes every day; derived from a random key that stays on your machine |
| Date | `"2026-11-25"` | The UTC day the row is about |

The row does not carry the install ID of the daily ping. With few users, a row could still be matched to a ping from the same day by version, OS and Python version, so do not read the daily ID as a guarantee of unlinkability.

#### What is never sent

- Lesson, decision, or playbook **content** (text, summaries, reasoning)
- User prompts or AI responses
- File paths (may reveal username or project names)
- Error messages, exception text, or stack traces
- Email, account details, command arguments, or device fingerprints
- Domain names or project names
- IP addresses are not stored (the server sees the source IP in transit)

#### Safety mechanisms

- Every value is either a fixed label, a bucket, or a number in a fixed format; a row with anything else is not sent, and the server rejects it too
- A tool name that is not one of Engram's registered tools is counted as `other`
- `engram telemetry status` shows whether the counts are on and which setting decided it; `engram telemetry preview` shows the exact next row
- Counts wait in a `piia-engram` folder under your user config directory (`stats-spool/`), never in your memory store, and are deleted after sending, after seven days, or when you turn telemetry off
- The send runs in the background; a failed send is skipped silently and never affects a tool

- **Off when:** exactly when the daily ping is off — `engram telemetry off` (or `engram telemetry remote off`), `ENGRAM_TELEMETRY=0`, `DO_NOT_TRACK=1` or `NO_TELEMETRY=1`; in CI, containers and tests — and also with `ENGRAM_TELEMETRY_REMOTE=0`, or if you ever turned the statistics or their remote sending off, whether through a command or a "no" in an earlier `engram setup`.
- **Endpoint:** `https://telemetry.piia-engram.com/v1/counts` (`ENGRAM_TELEMETRY_URL` replaces it). Rows are kept for 395 days.

#### 每日匿名使用计数（中文）

Engram 默认每天发送一次匿名使用计数：用了哪些工具、大致次数（分桶，不是精确数字）、错误类别（分桶）、记忆规模分桶、版本、系统、Python 主次版本、工具层级、安装时长分桶和日期。使用每天更换的 ID，不带每日信号的安装 ID。不发送内容、提示词、文件路径或错误正文；不保存 IP 地址。数据保留 395 天。

**行为变化：** 此前这些计数只在显式开启后发送，现在默认发送。以前退出过的选择继续有效。

查看：`engram telemetry status`（显示状态与原因）、`engram telemetry preview`（显示实际载荷）；关闭：`engram telemetry off`、`ENGRAM_TELEMETRY=0`、`DO_NOT_TRACK=1` 或 `NO_TELEMETRY=1`。CI、容器和测试环境自动不发。

### Optional local statistics log

`engram telemetry on` also turns on a local log of usage counts in `~/.engram/telemetry.log`. It is human-readable, stays on your machine and is never sent. `engram telemetry off` turns it off.

### Current status

The daily usage ping and the daily usage counts are on by default (turn them off as described above). The local statistics log stays off until you turn it on. Setup does not change these settings. Weekly feedback reports are opt-in and are sent only when an endpoint is configured; turn them off with `engram telemetry feedback off`.

### Optional feedback reports

A separate opt-in (`engram telemetry feedback on`, or a manual `engram feedback` after previewing with `engram feedback --dry-run`) sends a weekly aggregated report to help the project understand usage patterns. It is off unless you turn it on, has no built-in destination, and contains counts — never the content of a memory. Unlike the daily usage counts, it includes the labels of your most used knowledge domains with their counts, so preview it with `engram feedback --dry-run` before turning it on. Rate-limited to once per 7 days.

## Encryption

Optional field-level AES-256-GCM encryption is available for sensitive profile fields:

```bash
pip install piia-engram[secure]
export ENGRAM_SECRET="your-strong-passphrase"
```

- PBKDF2 with 600,000 iterations (OWASP 2023+ recommendation)
- Per-value random salt and nonce
- Encrypted fields stored as `enc:v2:...` in JSON files; legacy `enc:v1:...` values still decrypt
- Without `ENGRAM_SECRET`, piia-engram works normally with plaintext

## Access control

- All data is readable by any process with file-system access to `~/.engram/`
- `restricted_fields` filters sensitive profile fields from cold-start context
- Optional agent governance (`ENGRAM_GOVERNANCE=1`) adds self-reported caller trust levels and disclosure receipts; it is not a hardened sandbox or cryptographic caller identity
- Local audit logging is **on by default** — all read/write operations are recorded to `~/.engram/audit.log` (a local file, never sent anywhere); opt out with `ENGRAM_AUDIT=0`

**Recommendation:** Do not store passwords, API keys, or client PII in piia-engram. It is designed for personal AI context, not secrets management.

## Your rights

- **View**: All data is plain JSON — open any file in `~/.engram/`
- **Edit**: Modify any file directly; piia-engram reads on demand
- **Delete**: Remove any file or the entire `~/.engram/` directory
- **Export**: `get_identity_card` generates a portable Markdown summary
- **Disable telemetry**: `engram telemetry off` or set `ENGRAM_TELEMETRY=0`

## Contact

Questions about privacy practices? Open an issue at [github.com/Patdolitse/piia-engram](https://github.com/Patdolitse/piia-engram/issues).
