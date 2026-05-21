"""Prototype FastMCP server for dynamic tool registration.

This experiment intentionally lives outside the production MCP server. It
checks whether a running FastMCP instance can register another tool after the
initial tools/list response, and whether the server can notify connected
clients that the tool list changed.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

mcp = FastMCP("engram-dynamic-loading-prototype")

_secret_registered = False


@mcp.tool()
def hello(name: str = "world") -> str:
    """Return a greeting from the minimal always-on tool set."""
    return f"hello {name}"


async def secret_tool(code: str = "") -> str:
    """A tool registered only after activate_more is called."""
    return f"secret:{code or 'activated'}"


@mcp.tool()
async def activate_more(ctx: Context[ServerSession, Any] | None = None) -> dict[str, Any]:
    """Register secret_tool at runtime and try to notify the client."""
    global _secret_registered

    registered_now = False
    if not _secret_registered:
        mcp.add_tool(
            secret_tool,
            name="secret_tool",
            description="A dynamically registered proof-of-concept tool.",
        )
        _secret_registered = True
        registered_now = True

    notification = {
        "attempted": False,
        "sent": False,
        "error": None,
    }
    if ctx is not None:
        notification["attempted"] = True
        try:
            await ctx.session.send_tool_list_changed()
            notification["sent"] = True
        except Exception as exc:  # pragma: no cover - depends on live transport
            notification["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "registered_now": registered_now,
        "registered_tools": registered_tool_names(),
        "notification": notification,
    }


def registered_tool_names() -> list[str]:
    """Expose the current FastMCP tool registry for local smoke checks."""
    return sorted(mcp._tool_manager._tools.keys())


if __name__ == "__main__":
    mcp.run()

