# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 3.x     | Yes       |
| < 3.0   | No        |

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

- **Local-first** — All identity and knowledge data stays on the user's machine. No cloud sync. **Anonymous usage statistics are off by default** — users must explicitly opt in during `engram setup`. When enabled, aggregated counts (tool names + call counts, knowledge totals, engram version) are **logged to a local file only** (`~/.engram/telemetry.log`). **No network requests are made**. Never collected: identity content, prompts, file paths, IP addresses, email, or device fingerprint. Users can inspect the exact payload via `engram telemetry preview` and disable at any time with `engram telemetry off`. The optional `read_web_content` tool makes outbound HTTP to a local Reader service when explicitly invoked; core identity and knowledge tools make no network requests.
- **Encryption** — Sensitive profile fields (email, phone, location, etc.) are encrypted at rest using AES-256-GCM with PBKDF2-SHA256 key derivation. Requires `pip install piia-engram[secure]` and setting `ENGRAM_SECRET`.
- **Trust boundaries** — Users can restrict which profile fields are exposed to AI tools.
- **Setup file safety** — `engram setup` is read-only for external MCP client config files by default. Automatic client config updates require the explicit `--apply-external-config` flag and create backups plus a metadata-only file-safety ledger under the selected Engram data folder.
- **HTML escaping** — All user-controlled data in generated HTML (review page) is escaped to prevent XSS.
- **No eval / no exec** — No dynamic code execution from user data.
- **Audit logging** — All read/write operations are logged locally for traceability.

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
