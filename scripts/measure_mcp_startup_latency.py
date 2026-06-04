"""Measure Engram direct API and MCP stdio startup latency.

This script is intentionally local-only: it reads an Engram data directory,
starts a child MCP stdio server, calls one read tool, and prints JSON timing
evidence. It does not write Engram data and does not make network requests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min_ms": None, "median_ms": None, "max_ms": None}
    return {
        "min_ms": min(values),
        "median_ms": statistics.median(values),
        "max_ms": max(values),
    }


def _base_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC) if not existing_pythonpath else f"{SRC}{os.pathsep}{existing_pythonpath}"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENGRAM_HEARTBEAT_INTERVAL"] = "0"
    env["ENGRAM_TOOLS"] = args.tools
    if args.root:
        env["ENGRAM_DIR"] = str(args.root)
    if args.startup_sync:
        env["ENGRAM_MCP_STARTUP_SYNC"] = args.startup_sync
    return env


def measure_direct(args: argparse.Namespace) -> dict[str, Any]:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from piia_engram.core import Engram

    eng = Engram(root=args.root) if args.root else Engram()
    started = time.perf_counter()
    if args.tool == "get_resume_brief":
        output = eng.get_resume_brief(project_folder=args.project_folder or None)
    else:
        output = eng.generate_context(
            project_folder=args.project_folder,
            level=args.level,
        )
    return {
        "duration_ms": _ms(started),
        "output_bytes": len(str(output).encode("utf-8", errors="replace")),
    }


async def measure_mcp_once(args: argparse.Namespace) -> dict[str, Any]:
    server = StdioServerParameters(
        command=args.python,
        args=["-m", "piia_engram.mcp_server"],
        env=_base_env(args),
    )

    started = time.perf_counter()
    async with stdio_client(server) as (read_stream, write_stream):
        stdio_open_ms = _ms(started)
        async with ClientSession(read_stream, write_stream) as session:
            started = time.perf_counter()
            await session.initialize()
            initialize_ms = _ms(started)

            started = time.perf_counter()
            tools = await session.list_tools()
            list_tools_ms = _ms(started)

            payload: dict[str, Any]
            if args.tool == "get_resume_brief":
                payload = {"project_folder": args.project_folder, "token_budget": args.token_budget}
            else:
                payload = {
                    "project_folder": args.project_folder,
                    "level": args.level,
                    "token_budget": args.token_budget,
                }

            started = time.perf_counter()
            result = await session.call_tool(args.tool, payload)
            tool_call_ms = _ms(started)

    return {
        "stdio_open_ms": stdio_open_ms,
        "initialize_ms": initialize_ms,
        "list_tools_ms": list_tools_ms,
        "tool_call_ms": tool_call_ms,
        "tool_count": len(getattr(tools, "tools", [])),
        "result_bytes": len(str(result).encode("utf-8", errors="replace")),
    }


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    direct_runs = [measure_direct(args) for _ in range(args.runs)]
    mcp_runs = [await measure_mcp_once(args) for _ in range(args.runs)]

    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "runs": args.runs,
        "python": args.python,
        "root": str(args.root) if args.root else None,
        "project_folder": args.project_folder,
        "tool": args.tool,
        "level": args.level,
        "startup_sync": args.startup_sync or "background(default)",
        "direct_api": {
            "runs": direct_runs,
            "summary": _summary([r["duration_ms"] for r in direct_runs]),
        },
        "mcp_stdio": {
            "runs": mcp_runs,
            "initialize_summary": _summary([r["initialize_ms"] for r in mcp_runs]),
            "tool_call_summary": _summary([r["tool_call_ms"] for r in mcp_runs]),
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Optional Engram data directory to measure.")
    parser.add_argument("--project-folder", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--tool", choices=["get_user_context", "get_resume_brief"], default="get_user_context")
    parser.add_argument("--level", choices=["quick", "standard", "full"], default="standard")
    parser.add_argument("--token-budget", type=int, default=2000)
    parser.add_argument("--startup-sync", choices=["background", "eager", "off"], default=None)
    parser.add_argument("--tools", choices=["core", "all"], default="core")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    payload = asyncio.run(main_async(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
