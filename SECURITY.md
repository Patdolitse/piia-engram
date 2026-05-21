# Security Policy / 安全策略

## Supported Versions / 支持的版本

| Version | Supported |
|---------|-----------|
| 3.x     | Yes       |
| < 3.0   | No        |

## Reporting a Vulnerability / 报告安全漏洞

**Do NOT open a public issue for security vulnerabilities.**

**不要在公开 Issue 中报告安全漏洞。**

Please email **engram-security@proton.me** with:

请发送邮件至 **engram-security@proton.me**，包含：

- Description of the vulnerability / 漏洞描述
- Steps to reproduce / 复现步骤
- Impact assessment / 影响评估
- Suggested fix (if any) / 建议修复方案（如有）

We will acknowledge receipt within **48 hours** and aim to release a fix within **7 days** for critical issues.

我们将在 **48 小时**内确认收到，关键问题在 **7 天**内发布修复。

## Security Design / 安全设计原则

Engram is designed with security in mind:

- **100% local** — All data stays on the user's machine. No cloud, no telemetry, no external calls.
- **Encryption** — Sensitive profile fields (email, phone, location, etc.) are encrypted at rest using Fernet symmetric encryption.
- **Trust boundaries** — Users can restrict which profile fields are exposed to AI tools.
- **HTML escaping** — All user-controlled data in generated HTML (review page) is escaped to prevent XSS.
- **No eval / no exec** — No dynamic code execution from user data.
- **Audit logging** — All read/write operations are logged locally for traceability.

## Scope / 范围

The following are in scope for security reports:

- XSS, injection, or path traversal in any Engram output
- Encryption key leakage or weak cryptography
- Data exposure through MCP tool responses
- Unauthorized access to restricted profile fields

Out of scope:

- Attacks requiring physical access to the user's machine (Engram is a local tool)
- Denial of service against the local MCP server
- Issues in third-party dependencies (report upstream, but let us know too)
