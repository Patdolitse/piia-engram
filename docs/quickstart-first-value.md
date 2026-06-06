# Quickstart: first value in about 5 minutes

Goal: save one useful lesson and recall it in a fresh AI session using the
default **17 core tools**. You do not need the 67 advanced tools or
`ENGRAM_TOOLS=all` for this path; the default is `ENGRAM_TOOLS=core`.

This quickstart is for a local MCP-compatible coding tool such as Claude Code,
Codex, Cursor, Windsurf, or Claude Desktop. Setup details can vary by MCP host.
For host-specific setup, start with [Claude Code](integrations/claude-code.md),
[Codex](integrations/codex.md), or [Cursor](integrations/cursor.md). For the
tool tiers and owner-gated surfaces, keep the
[operator MCP cheatsheet](operator-mcp-cheatsheet.md) nearby.

## 1. Install

```bash
pip install piia-engram
engram setup
```

Setup detects local AI tool configuration in read-only mode. It prints guidance
by default and does not modify external client config files unless you choose
the explicit opt-in path:

```bash
engram setup --apply-external-config
```

Identity and knowledge tools use local files. No cloud account is required.

## 2. Check health

```bash
engram doctor
```

A healthy setup reports that the MCP server is available and that the Engram
data folder is readable. The report is for local diagnostics; review it before
sharing because private diagnostics can include local paths.

## 3. Give Engram one stable preference

In your connected AI tool, ask it to record a simple preference, for example:

```text
Remember that I prefer concise answers with explicit verification commands.
```

The AI can call one of the core write tools:

- `memory_store`
- `add_lesson`
- `add_decision`
- `add_playbook`
- `update_identity`

New AI-suggested knowledge starts as `staging`; it is verified only after you approve. That review step is intentional: durable memory is a user-owned asset, not an agent scratchpad.

## 4. Recall it in a new session

Start a fresh chat in the same configured tool, or in another configured MCP
tool on the same machine. The AI should call a core read/startup tool such as:

- `get_user_context`
- `get_recall`
- `search_knowledge`
- `get_relevant_knowledge`
- `get_resume_brief`

The first value moment is simple: the new session can start from the preference
or lesson you already gave, instead of asking you to explain it again.

## 5. The core tools you just used

| Job | Core tools |
|---|---|
| Startup and recovery | `get_user_context`, `get_recall`, `get_resume_brief`, `get_recent_context`, `get_daily_log` |
| Read/search | `search_knowledge`, `get_relevant_knowledge` |
| Write/update | `memory_store`, `add_lesson`, `add_decision`, `add_playbook`, `update_identity` |
| Project context | `get_project_context`, `save_project_snapshot` |
| Session end | `wrap_up_session` |
| Diagnostics | `doctor` |

`get_identity_card` is also in the core tier because it is frequently needed for
non-MCP handoffs, but it is an owner-gated export surface rather than a normal
read/search tool.

The advanced set exists for review, import/export, governance, migration, and
management workflows. Most first-time users should leave it off until they need
those workflows.

## When to enable all tools

Leave the default core surface on for first value, daily recall, and normal
session wrap-up. Enable all tools only when you intentionally need review
queues, imports/exports, Playbook maintenance, local tool-registry management,
or proposal-only context-governance previews:

```bash
ENGRAM_TOOLS=all
```

Some advanced tools are owner/admin/export surfaces. Turning them on increases
what the host can see in the tool list; it does not remove owner gates,
governance checks, or the requirement to confirm public actions yourself.

## If recall did not fire

First confirm the client can see the MCP server: run `engram doctor`, restart
the AI tool, and ask it to call `get_resume_brief` or `get_user_context`.

If the tool is connected but the answer still ignores memory, make the recall
explicit once:

```text
Use Engram to search for my saved preference about concise answers.
```

If explicit search works but proactive recall does not, treat the client as
L2 read/search capable, not L3 or L4 behavior-verified. That is still useful
first value; it just means public claims should stay below behavior-gain or
cross-client continuity levels until a validation run proves more.

## Next steps

- Read [Trust evidence](trust-evidence.md) to see how public claims are checked.
- Read [Trust model](trust.md) for data boundaries and what not to store.
- Run `python demos/cross_tool_continuity_demo.py --json` for a synthetic
  cross-tool handoff proof.
- Read [Comparison](comparison.md) to understand where piia-engram sits among
  agent memory databases, repo rule files, and native tool memories.
