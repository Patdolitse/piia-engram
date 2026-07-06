"""Local-only wrap_up_session timing benchmark; not a public performance claim."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any


os.environ.setdefault("ENGRAM_MCP_STARTUP_SYNC", "off")
os.environ.setdefault("ENGRAM_HEARTBEAT_INTERVAL", "0")
os.environ.setdefault("ENGRAM_TELEMETRY", "0")
os.environ.setdefault("ENGRAM_FEEDBACK", "0")
os.environ.setdefault("ENGRAM_TEST", "1")

from piia_engram import mcp_server  # noqa: E402
from piia_engram.core import Engram  # noqa: E402


def _stop_session_heartbeat() -> None:
    session = getattr(mcp_server, "_session", None)
    stop_event = getattr(session, "_stop_event", None)
    if stop_event is not None:
        stop_event.set()
    thread = getattr(session, "_heartbeat_thread", None)
    if thread is not None:
        thread.join(timeout=2.0)


def _install_temp_runtime(store: Path) -> None:
    os.environ["ENGRAM_DIR"] = str(store)
    store.mkdir(parents=True, exist_ok=True)
    (store / ".bootstrap_done").write_text("1", encoding="utf-8")
    _stop_session_heartbeat()
    mcp_server._engram = Engram(root=store)
    mcp_server._session = mcp_server._SessionTracker()
    mcp_server._tracker = None


async def _run_sample(project: Path, index: int) -> dict[str, Any]:
    raw = await mcp_server.wrap_up_session(
        summary=(
            f"Local benchmark sample {index}. "
            "Completed: measured default lightweight closeout timing. "
            "Next: keep reconcile disabled unless owner explicitly requests it."
        ),
        source_tool="bench_wrap_up_session",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=False,
    )
    payload = json.loads(raw)
    maintenance = payload.get("maintenance") or {}
    return {
        "sample": index,
        "timing": payload.get("timing") or {},
        "maintenance": {
            "reconcile_memories": (maintenance.get("reconcile_memories") or {}).get("status"),
            "reconcile_ai_configs": (maintenance.get("reconcile_ai_configs") or {}).get("status"),
        },
    }


async def run_benchmark(samples: int = 3) -> dict[str, Any]:
    sample_count = max(1, int(samples))
    with tempfile.TemporaryDirectory(prefix="engram-wrapup-bench-") as tmp:
        root = Path(tmp)
        store = root / "store"
        project = root / "project"
        project.mkdir()
        _install_temp_runtime(store)
        results = [await _run_sample(project, i + 1) for i in range(sample_count)]

    totals = [int((item.get("timing") or {}).get("total_ms") or 0) for item in results]
    return {
        "schema": "wrap_up_session_timing_benchmark.v1",
        "note": "Local-only timing baseline; not a public performance claim.",
        "samples": results,
        "summary": {
            "count": len(results),
            "total_ms": {
                "min": min(totals),
                "median": int(statistics.median(totals)),
                "max": max(totals),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--samples", type=int, default=3, help="Number of samples to run.")
    args = parser.parse_args()

    payload = asyncio.run(run_benchmark(samples=args.samples))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        total = payload["summary"]["total_ms"]
        print("wrap_up_session local timing baseline")
        print(payload["note"])
        print(
            f"samples={payload['summary']['count']} "
            f"total_ms min/median/max={total['min']}/{total['median']}/{total['max']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
