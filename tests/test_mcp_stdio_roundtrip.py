"""Real MCP stdio JSON-RPC roundtrip smoke test.

Almost every test drives the tools in-process through the wrapper layer. This
is the one test that launches ``piia_engram.mcp_server`` as a real subprocess
and runs a full JSON-RPC roundtrip over stdio (initialize -> tools/list ->
tools/call) with the official MCP client — the same transport Claude Code,
Codex and Cursor use. A green in-process suite does not prove a client can
actually reach the server over the protocol; this test guards that path.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# ``mcp`` is a core runtime dependency, so it is always importable wherever the
# package is installed. Import it directly (no importorskip): if it ever goes
# missing this test must turn red, not silently skip.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent

# Upper bound for the whole handshake + two roundtrips. A healthy server is far
# faster; exceeding this means the entry point is unreachable (fail, not skip).
_ROUNDTRIP_TIMEOUT_S = 30


def _server_params(tmp_path: Path) -> StdioServerParameters:
    env = os.environ.copy()
    env.update(
        {
            "ENGRAM_DIR": str(tmp_path / "store"),
            "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home"),
            # Skip startup auto-migrate / reconcile sync: fast boot, zero side effects.
            "ENGRAM_EPHEMERAL": "1",
            "ENGRAM_TOOLS": "core",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    # Let the child initialize for real, like a live client connection.
    env.pop("ENGRAM_TEST", None)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "piia_engram.mcp_server"],
        env=env,
    )


async def _initialize_list_call(
    params: StdioServerParameters,
    project_folder: Path,
):
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            call = await session.call_tool("get_user_context", {"level": "quick"})
            closeout = await session.call_tool(
                "wrap_up_session",
                {
                    "summary": "Completed an isolated MCP stdio closeout smoke test.",
                    "project_folder": str(project_folder),
                    "source_tool": "pytest",
                    "project_title": "MCP stdio smoke",
                    "user_confirmed": True,
                    "run_reconcile": False,
                    "idempotency_key": "mcp-stdio-roundtrip-closeout",
                },
            )
            return init, tools, call, closeout


def _run_roundtrip(params: StdioServerParameters, project_folder: Path):
    return asyncio.run(
        asyncio.wait_for(
            _initialize_list_call(params, project_folder),
            timeout=_ROUNDTRIP_TIMEOUT_S,
        )
    )


def test_mcp_server_stdio_jsonrpc_roundtrip(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "mcp-stdio-smoke"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    init, tools, call, closeout = _run_roundtrip(
        _server_params(tmp_path),
        project,
    )

    # Handshake succeeded and we reached the engram server itself.
    assert init.serverInfo.name == "engram", f"unexpected serverInfo: {init.serverInfo!r}"
    assert init.protocolVersion, f"missing protocolVersion: {init!r}"

    # tools/list roundtrips a non-empty set with the cold-start core tool present.
    tool_names = {t.name for t in tools.tools}
    assert "get_user_context" in tool_names, f"core tool missing: {sorted(tool_names)}"

    # tools/call roundtrips: a real read-only entry actually executes, no error.
    assert call.isError is False, f"tool call errored: {call!r}"
    assert call.content, f"empty tool call content: {call!r}"

    assert closeout.isError is False, f"closeout errored: {closeout!r}"
    payload = json.loads(closeout.content[0].text)
    assert payload["maintenance"]["save_project_snapshot"]["status"] == "ok"
    assert payload["operation"]["status"] in {"completed", "partial_complete"}
