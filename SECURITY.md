# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 4.x     | Yes       |
| < 4.0   | No        |

## Reporting a Vulnerability

**Do NOT open a public issue for security vulnerabilities.**

Please email **engram-security@proton.me** with:

- Description of the vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if any)

We will acknowledge receipt within **48 hours** and aim to release a fix within **7 days** for critical issues.

## Security Design

Engram is designed with security in mind:

- **Local-first by default** — Identity and knowledge data stays on the user's machine. There is no cloud sync. Core identity, knowledge, search, review, and governance tools use the local Engram data folder and make no network requests.
- **Telemetry is off by default** — Anonymous usage statistics require an explicit opt-in during `engram setup` or with `engram telemetry on`.
- **Local telemetry** — When local telemetry is enabled, count-only usage metadata is written to `~/.engram/telemetry.log`. Local telemetry by itself makes no network requests.
- **Remote telemetry** — Remote sending is a separate opt-in (`engram telemetry remote on`) and requires local telemetry to be enabled first. It sends the same count-only, metadata-only payload to the telemetry endpoint configured via the `ENGRAM_TELEMETRY_URL` environment variable. The open-source core ships with **no built-in telemetry endpoint** — there is no hardcoded default destination — so even after opting in, remote sending stays inactive until an operator sets `ENGRAM_TELEMETRY_URL`. It can be disabled with `engram telemetry remote off`.
- **Feedback reports** — Weekly feedback reports are a third, separate opt-in (`engram telemetry feedback on`, or a manually previewed `engram feedback --dry-run` flow). They send count-only anonymous feedback to the feedback endpoint configured via the `ENGRAM_FEEDBACK_URL` environment variable. As with remote telemetry, the core ships with **no built-in feedback endpoint**, so sending stays inactive until an operator sets `ENGRAM_FEEDBACK_URL`; reports are also rate-limited.
- **Payload preview** — Users can inspect the next telemetry payload with `engram telemetry preview` before logging or sending it, and disable telemetry at any time with `engram telemetry off`.
- **Never collected on any telemetry or feedback path** — identity content, lesson/decision/playbook bodies, prompts, AI responses, project paths, file paths, credentials, email, IP-derived identifiers, device fingerprints, domain names, free-text content, error messages, exception text, or stack traces.
- **Optional web reads** — The optional `read_web_content` tool makes outbound HTTP only when explicitly invoked for a URL. This is separate from identity/knowledge storage and telemetry.
- **Test and release data boundary** — Tests, demos, benchmarks, release evidence, and client-validation harnesses must use synthetic fixtures or an isolated temporary `ENGRAM_DIR`. Real user stores (`~/.engram` or owner-selected production data folders) must not be copied into public artifacts, screenshots, release evidence, or package fixtures.
- **Optional field-level encryption** — By default, Engram stores local identity and knowledge files as plaintext JSON/Markdown for inspectability and portability. Supported sensitive profile fields (email, phone, location, etc.) can be encrypted at rest using AES-256-GCM with PBKDF2-SHA256 key derivation. Requires `pip install piia-engram[secure]` and setting `ENGRAM_SECRET`; this is not whole-store encryption.
- **Trust boundaries** — Users can restrict which profile fields are exposed to AI tools.
- **Setup file safety** — `engram setup` lists the exact external MCP client config files it will touch and asks for a one-keystroke confirm before writing the MCP connection; declining leaves every external config untouched. `--apply-external-config` skips the prompt for non-interactive/CI runs. Either way, writes create backups plus a metadata-only file-safety ledger under the selected Engram data folder.
- **HTML escaping** — All user-controlled data in generated HTML (review page) is escaped to prevent XSS.
- **No eval / no exec** — No dynamic code execution from user data.
- **Audit logging (on by default)** — All read/write operations are recorded locally to `~/.engram/audit.log`, a plain JSON-lines file for traceability. On by default; opt out with `ENGRAM_AUDIT=0`. It is a local file only and is never sent anywhere. This is distinct from the optional governance *disclosure ledger* (`ENGRAM_GOVERNANCE=1`), which is the SHA-256 hash-chained log.

## Scope

The following are in scope for security reports:

- XSS, injection, or path traversal in any Engram output
- Encryption key leakage or weak cryptography
- Data exposure through MCP tool responses
- Unauthorized access to restricted profile fields

Out of scope:

- Attacks requiring physical access to the user's machine (Engram is a local tool)
- Denial of service against the local MCP server
- Issues in third-party dependencies (report upstream, but let us know too)
