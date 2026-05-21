# MCP Dynamic Tool Scheduling Research Report

## Scope

This report evaluates three options for making Engram's MCP surface easier for AI agents to use:

- A. Dynamic tool loading
- B. One universal router tool
- C. A small coordinator layer of high-level tools

All prototypes are isolated under `experiments/`. Production files such as `mcp_server.py`, `core.py`, and `setup_wizard.py` were not modified.

## Comparison

| Option | Prototype | Result | Main upside | Main risk |
| --- | --- | --- | --- | --- |
| A. Dynamic tool loading | `experiments/dynamic_loading/test_server.py` | Partially feasible | Keeps the initial tool list small and can reveal tools on demand | MCP host/client refresh behavior is not guaranteed |
| B. Universal router | `experiments/router/router_tool.py` | Partially feasible | Compresses many MCP tools into one tool call | Tool schema clarity moves into prompt text and routing logic |
| C. Coordinator | `experiments/coordinator/high_level_actions.py` | Feasible and recommended | Gives agents a small, intent-shaped API while preserving internal orchestration | Requires choosing and maintaining the right high-level action boundaries |

## A. Dynamic Tool Loading

### Prototype

`experiments/dynamic_loading/test_server.py` starts with:

- `hello`
- `activate_more`

When `activate_more` runs, it registers `secret_tool` with `FastMCP.add_tool(...)`.

### Local SDK findings

- `FastMCP.add_tool(...)` exists and mutates the live `ToolManager`.
- `ToolManager.add_tool(...)` does not automatically send `notifications/tools/list_changed`.
- The MCP server session layer exposes `send_tool_list_changed()`.
- A FastMCP tool can call `ctx.session.send_tool_list_changed()` from a live request context.
- The Python client session implementation checked locally receives notifications, but does not obviously refresh tools automatically on `tools/list_changed`.

### External doc check

Context7 documentation for the official Model Context Protocol Python SDK shows FastMCP notification examples through `Context` and `ServerSession`, including manual list-change notifications for resources. The same server session pattern is available locally for tools via `send_tool_list_changed()`.

### Conclusion

Partially feasible.

The server side works for runtime registration. The important unknown is MCP host behavior: Claude Code, Cursor, or other clients may not refresh their visible tool list automatically after `notifications/tools/list_changed`. This needs real host testing before production use.

## B. Universal Router

### Prototype

`experiments/router/router_tool.py` implements:

```python
engram_action(intent, params, core=None)
```

Supported routed intents:

- `search`
- `add_lesson`
- `add_decision`
- `get_context`
- `cleanup`

The tests cover English aliases, Chinese aliases, typo aliases, unknown intent fallback, context retrieval, and cleanup candidate discovery.

### Findings

- The router can reduce a large MCP tool list to one visible tool.
- It handles direct intents and common natural-language aliases.
- It provides a clear fallback for unknown intents.
- It is easy to test with a fake core and does not require MCP protocol changes.

### Problems

- The AI has to understand and fill a generic `intent + params` contract.
- Per-action schemas become implicit instead of visible in MCP tool metadata.
- Mistakes move from "wrong tool chosen" to "wrong intent or params generated".
- Tool descriptions would become long and brittle as Engram capabilities grow.

### Conclusion

Partially feasible.

It is useful as an internal compatibility layer or emergency simplification, but it should not be the only public MCP interface.

## C. Coordinator

### Prototype

`experiments/coordinator/high_level_actions.py` defines five high-level actions:

- `remember(content, context=None)`
- `recall(query, project=None)`
- `cleanup(scope="all")`
- `inherit(project_description)`
- `sync(project=None)`

These actions orchestrate lower-level core calls while presenting a compact, meaningful MCP surface.

### Findings

- The model gets a small number of concrete tools instead of one vague router or dozens of atomic tools.
- Each high-level tool can keep a clear schema and docstring.
- The lower-level Engram APIs can remain available internally.
- Tests verify duplicate checks, lesson/decision classification, project recall, cleanup planning, inheritance, and session sync.
- This matches Engram's real usage pattern better than purely atomic tools: users usually ask to remember, recall, inherit, clean, or initialize context.

### Problems

- Requires product judgment about what belongs in each high-level action.
- Some advanced maintenance/debug tools may still need a separate advanced tier.

### Conclusion

Feasible and recommended.

This is the best next production direction: keep a compact high-level default MCP surface, preserve lower-level tools as internal or advanced APIs, and avoid depending on dynamic client refresh behavior.

## Recommendation

Implement option C first.

Suggested production shape:

- Default tier: high-level coordinator tools such as `remember`, `recall`, `sync`, `cleanup`, and `inherit`.
- Advanced tier: existing lower-level tools for precise maintenance, debugging, import/export, and project graph operations.
- Optional internal router: reuse option B only where a single dispatch entrypoint is useful.
- Defer dynamic loading: revisit option A only after real MCP host testing proves `tools/list_changed` refresh behavior in the target clients.

## Open Questions

- Do Claude Code and Cursor refresh tool lists automatically after `notifications/tools/list_changed`?
- Should high-level coordinator tools replace the current `ENGRAM_TOOLS=core` surface, or become a new tier such as `ENGRAM_TOOLS=coordinator`?
- Which lower-level tools must remain visible for power users?
- Should `cleanup` ever auto-merge, or should it always return a review plan first?

## Validation

Run:

```powershell
python -m pytest -q experiments
```

Expected result after the prototype tests pass:

- Router tests validate the universal dispatch approach.
- Coordinator tests validate the recommended orchestration approach.
- Dynamic loading remains a manual MCP client compatibility test documented in `experiments/dynamic_loading/HOW_TO_TEST.md`.

