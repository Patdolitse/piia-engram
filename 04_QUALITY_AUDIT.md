# Phase 5 — Quality Audit

## 1. TODO/FIXME/XXX/HACK Search

**Command:** `grep -r "TODO\|FIXME\|XXX\|HACK" src/ -n`

**Result:** No matches found in `src/`.

✅ The codebase has no TODO/FIXME/XXX/HACK markers — indicating well-scoped, completed work.

---

## 2. Broken Link Check (README and docs)

### README.md links checked

| Link | Target | Status |
|---|---|---|
| `docs/comparison.md` | `docs/comparison.md` | ✅ Exists |
| `docs/coverage_baseline_v3.14.2.md` | `docs/coverage_baseline_v3.14.2.md` | ✅ Exists |
| `docs/architecture.md` | `docs/architecture.md` | ✅ Exists |
| `docs/telemetry_roadmap.md` | `docs/telemetry_roadmap.md` | ✅ Exists |
| `docs/milestone_review_v3.13.2.md` | `docs/milestone_review_v3.13.2.md` | ✅ Exists |
| `docs/milestone_review_v3.14.3.md` | `docs/milestone_review_v3.14.3.md` | ✅ Exists |
| `SECURITY.md` | `SECURITY.md` | ✅ Exists |
| `CONTRIBUTING.md` | `CONTRIBUTING.md` | ✅ Exists |
| `CHANGELOG.md` | `CHANGELOG.md` | ✅ Exists |

### External links checked

| Link | Target | Status |
|---|---|---|
| `https://modelcontextprotocol.io` | MCP homepage | ✅ Accessible |
| `https://pypi.org/project/piia-engram/` | PyPI package | ✅ Accessible |
| `https://docs.anthropic.com/en/docs/claude-code/memory` | Claude Code memory docs | ✅ Accessible |
| `https://github.com/letta-ai/letta` | Letta GitHub | ✅ Accessible |
| `https://docs.letta.com` | Letta docs | ✅ Accessible |
| `https://github.com/mem0ai/mem0` | Mem0 GitHub | ✅ Accessible |
| `https://docs.mem0.ai` | Mem0 docs | ✅ Accessible |
| `https://docs.cline.bot/features/memory` | Cline memory docs | ✅ Accessible |

✅ All README and docs links resolve correctly.

---

## 3. Configuration Validation

### Environment Variable Validation

| Env Var | Used In | Validation Present? | Notes |
|---|---|---|---|
| `ENGRAM_SECRET` | `crypto.py`, `core.py` | ⚠️ Partial — only checks truthy string, no format enforcement | Accepts any non-empty string; no minimum length check |
| `ENGRAM_AUDIT` | `core.py` | ✅ Yes — checks against `("1", "true", "yes")` | Good allowlist |
| `ENGRAM_TOOLS` | `mcp_server.py` | ⚠️ Weak — only checks `!= "core"` to enable all tools | No allowlist; accepts any string |
| `ENGRAM_AUTH_TOKEN` | `mcp_server.py` | ⚠️ Used for Bearer auth but no format/length validation | Used in `secrets.compare_digest` — token generation is the user's responsibility |
| `ENGRAM_CORS_ORIGINS` | `mcp_server.py` | ❓ Not seen in code — likely in SSE handler | Documentation exists in README but not confirmed in code |
| `ENGRAM_REMOTE_URL` | README mentions it | ⚠️ Not validated in code | Only appears in docs |

### Path Validation

`mcp_server.py:_validate_path()` (lines 132-154) validates:
- ✅ Null bytes (`\x00`) — prevents truncation attacks
- ✅ Empty/whitespace strings — catches programming bugs
- ⚠️ No size limit on path string
- ❌ No traversal check (e.g., `../`) — but this is intentional since Engram is not a sandbox

### MCP Tool Input Validation

- `telemetry.py:139-155`: Validates string **values** but not dictionary **keys** (Issue #1 from cross-AI report)
- `mcp_server.py` tools: Most tools pass args to `core.py` methods without additional validation
- `read_file` tool: No file size limit or extension whitelist

---

## 4. Stale Documentation (from Cross-AI Report)

| File | Problem | Impact |
|---|---|---|
| `CONTRIBUTING.md:28` | Still says "no telemetry" despite opt-in telemetry existing since v3.15.0 | Low — misleading contributor expectations |
| `docs/comparison.md:34` | Says "386 tests, 78% coverage (v3.14.2)" | Low — factual inaccuracy vs current 657 tests / 83% |
| `docs/telemetry_roadmap.md:55` | Calls Phase 2 "v3.16.0" despite code being Phase 1 local-only | Low — incorrect version label for future milestone |
| `docs/architecture.md:77-80` | Module line counts stale for `mcp_server.py`, `telemetry.py`, `setup_wizard.py` | Low — documentation drift |
| `README.md:349` | `core.py` line count says 1088, actual is 1097 | Low — minor numeric inaccuracy |
| `README.md:348` | Says "490 tests" but actual count is 657 | Medium — significant discrepancy |

---

## 5. Code Quality Observations

### Strengths

1. **No TODO/FIXME/HACK** — clean production code
2. **File locking** via `portalocker` for concurrent write safety
3. **Audit logging** with opt-in flag and JSON-lines format
4. **AES-256-GCM encryption** with PBKDF2 600k for optional field-level encryption
5. **Staged knowledge** (staging → verified) with manual promotion gate
6. **Conflict detection** for contradictory decisions/lessons
7. **XSS protection** in review page HTML output (`reports_review.py`)
8. **NUL byte rejection** in path validation

### Concerns

1. **`mcp_server.py` at 1411 lines** — large monolith; consider splitting into tool-category modules
2. **`_apply_tool_tier()` fragile** — uses `getattr` introspection on FastMCP internals; could break on FastMCP update
3. **Telemetry dict-key validation missing** — the cross-AI evaluation flagged this as medium severity
4. **No type hints enforced** — mypy not in CI, type annotations present but not verified
5. **`demos/cross_tool_demo.py` lint violations** — E402 (import order), F541 (f-string without placeholders)
6. **`ENGRAM_TOOLS` accepts any string** — no allowlist; effectively bypasses tier restriction
7. **`read_file` tool lacks size/extension limits** — no DoS protection on large file reads

---

## 6. Security Notes

| Area | Status | Notes |
|---|---|---|
| File locking | ✅ | `portalocker` for atomic writes |
| Encryption at rest | ✅ | AES-256-GCM, PBKDF2 600k, opt-in via `ENGRAM_SECRET` |
| Auth token | ⚠️ | `secrets.compare_digest` used correctly; no format enforcement |
| XSS in review page | ✅ | User data escaped in HTML output |
| Path traversal | ✅ | Intentional — Engram is not a sandbox; user controls own files |
| NUL bytes in paths | ✅ | Rejected in `_validate_path` |
| Telemetry content leak | ⚠️ | Values validated, keys not; medium risk for Phase 2 |
| Anonymous stats | ✅ | Off by default; opt-in during setup; no content/prompts/paths |

---

## Summary

| Category | Finding count |
|---|---|
| TODO/FIXME/HACK in src/ | 0 ✅ |
| Broken links in README/docs | 0 ✅ |
| Configuration validation gaps | 5 ⚠️ |
| Stale documentation items | 6 (1 medium, 5 low) |
| Code quality concerns | 7 (mix of low/medium) |
| Security concerns | 1 medium, rest low/acceptable |