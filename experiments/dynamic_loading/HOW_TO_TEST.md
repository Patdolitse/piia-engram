# Dynamic Loading Prototype: How To Test

This prototype is intentionally isolated from production Engram code.

## Files

- `experiments/dynamic_loading/test_server.py`

## Local smoke check

From the repo root:

```powershell
python - <<'PY'
from experiments.dynamic_loading.test_server import activate_more, registered_tool_names
import asyncio

print(registered_tool_names())
print(asyncio.run(activate_more()))
print(registered_tool_names())
PY
```

Expected result:

- Before activation: `hello` and `activate_more`.
- After activation: `secret_tool` appears in the in-process FastMCP registry.
- Without a live MCP request context, no notification is sent.

## MCP client check

Add the prototype server to an MCP client config, for example:

```json
{
  "mcpServers": {
    "engram-dynamic-loading-prototype": {
      "command": "python",
      "args": [
        "E:/Personal Intelligence Identity Asset/engram/experiments/dynamic_loading/test_server.py"
      ]
    }
  }
}
```

Then test this sequence:

1. Connect the client and list tools.
2. Confirm only `hello` and `activate_more` are visible.
3. Call `activate_more`.
4. Watch whether the client refreshes its tool list after the server sends `notifications/tools/list_changed`.
5. Try calling `secret_tool`.

## Research Findings

- `FastMCP.add_tool(...)` can register a tool after startup because it mutates the live `ToolManager`.
- FastMCP's `ToolManager.add_tool(...)` does not automatically send a `tools/list_changed` notification.
- A live FastMCP tool can manually call `ctx.session.send_tool_list_changed()`.
- The low-level MCP server/session layer has explicit support for `ToolListChangedNotification`.
- Client behavior is the unknown part: Python `ClientSession` receives notifications, but the checked implementation does not automatically refresh the visible tool list on `tools/list_changed`.

## Current conclusion

Partially feasible. Server-side dynamic registration is possible, but the useful end-user behavior depends on whether each MCP host refreshes tools after `notifications/tools/list_changed`.

