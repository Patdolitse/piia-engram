# Campaign State

**Repository**: Patdolitse/engram
**Fork**: okwn/engram
**Default Branch**: main
**License**: Apache-2.0
**Archived**: No
**PyPI Package**: piia-engram
**Latest Version**: 3.16.1

## Upstream Info
- **URL**: https://github.com/Patdolitse/engram
- **Stars**: 61
- **Forks**: 2
- **Language**: Python
- **Topics**: ai-agent, ai-identity, ai-memory, ai-tools, anthropic, claude-code, claude-desktop, codex, context-management, cursor, developer-tools, knowledge-management, llm-tools, local-first, mcp, mcp-server, model-context-protocol, persistent-memory, personal-ai, python

## Repository Stats
- **Schema version**: v2.0 (migrated from v1)
- **MCP tools**: 43 total (10 Tier-1 default, 33 opt-in via `ENGRAM_TOOLS=all`)
- **Tests**: 490+ passing
- **Code coverage**: 83%+ total
- **Python support**: 3.10, 3.11, 3.12, 3.13
- **CI**: GitHub Actions (Ubuntu, macOS, Windows; all Python versions)

## Security
- **Contact**: engram-security@proton.me (no public issues)
- **Encryption**: AES-256-GCM + PBKDF2 600k iterations (opt-in via `ENGRAM_SECRET`)
- **Audit logging**: opt-in via `ENGRAM_AUDIT=1`

## Remote Deployment
- SSE transport mode: `python -m engram_core.mcp_server --transport sse --host 0.0.0.0 --port 8767`
- Auth: Bearer token via `ENGRAM_AUTH_TOKEN`
- Optional `uvicorn>=0.20` via `pip install piia-engram[remote]`
