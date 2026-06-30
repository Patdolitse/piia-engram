"""Owner-run wrap_up_session diagnostic; defaults to an isolated temp store."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("ENGRAM_MCP_STARTUP_SYNC", "off")
os.environ.setdefault("ENGRAM_HEARTBEAT_INTERVAL", "0")
os.environ.setdefault("ENGRAM_TELEMETRY", "0")
os.environ.setdefault("ENGRAM_FEEDBACK", "0")
os.environ.setdefault("ENGRAM_TEST", "1")

from piia_engram import mcp_server  # noqa: E402
from piia_engram.core import Engram  # noqa: E402


logging.getLogger("piia_engram").setLevel(logging.CRITICAL)
logging.getLogger("piia_engram.mcp_tools_admin").setLevel(logging.CRITICAL)


@contextmanager
def _temporary_env(name: str, value: str):
    prior = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior


def _stop_session_heartbeat() -> None:
    session = getattr(mcp_server, "_session", None)
    stop_event = getattr(session, "_stop_event", None)
    if stop_event is not None:
        stop_event.set()
    thread = getattr(session, "_heartbeat_thread", None)
    if thread is not None:
        thread.join(timeout=2.0)


def _install_runtime(store: Path, *, write_bootstrap: bool = True) -> None:
    store.mkdir(parents=True, exist_ok=True)
    if write_bootstrap:
        (store / ".bootstrap_done").write_text("1", encoding="utf-8")
    os.environ["ENGRAM_DIR"] = str(store)
    _stop_session_heartbeat()
    mcp_server._engram = Engram(root=store)
    mcp_server._session = mcp_server._SessionTracker()
    mcp_server._tracker = None


async def _run_closeout(project: Path, synthetic_error: bool) -> dict[str, Any]:
    eng = mcp_server._engram
    if synthetic_error:
        private_path = "E:" + "\\" + "\\".join([
            "Workspace With Spaces",
            "project",
            "secret.json",
        ])

        def fail_insights(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError(f"cannot read {private_path}")

        eng.extract_session_insights = fail_insights  # type: ignore[method-assign]

    raw = await mcp_server.wrap_up_session(
        summary="Diagnostic closeout sample. Completed: stage timing probe.",
        source_tool="diagnose_wrap_up_session",
        project_folder=str(project),
        user_confirmed=True,
        run_reconcile=False,
    )
    return json.loads(raw)


def _live_inspect_payload() -> dict[str, Any]:
    return {
        "schema": "wrap_up_session_diagnostic.v1",
        "store_mode": "live_inspect",
        "live_store": True,
        "writeful": False,
        "timing": {},
        "maintenance": {
            "live_inspect": {
                "status": "ok",
                "note": "read-only aggregate metadata only",
            },
        },
        "errors": {},
    }


async def run_diagnostic(
    *,
    live_closeout: bool,
    live_inspect: bool,
    synthetic_error: bool,
) -> dict[str, Any]:
    if live_inspect:
        return _live_inspect_payload()

    if live_closeout:
        store = Path(os.environ.get("ENGRAM_DIR") or Path.home() / ".engram")
        project = Path.cwd()
        _install_runtime(store, write_bootstrap=False)
        payload = await _run_closeout(project, synthetic_error)
        store_mode = "live"
        writeful = True
    else:
        with tempfile.TemporaryDirectory(prefix="engram-wrapup-diagnostic-") as tmp:
            root = Path(tmp)
            store = root / "store"
            project = root / "project"
            project.mkdir()
            _install_runtime(store)
            payload = await _run_closeout(project, synthetic_error)
        store_mode = "isolated"
        writeful = False

    return {
        "schema": "wrap_up_session_diagnostic.v1",
        "store_mode": store_mode,
        "live_store": bool(live_closeout),
        "writeful": writeful,
        "timing": payload.get("timing") or {},
        "maintenance": payload.get("maintenance") or {},
        "errors": {
            "insights": (payload.get("insights") or {}).get("error"),
            "project_snapshot": (payload.get("project_snapshot") or {}).get("error"),
        },
    }


async def run_compare_fast(*, synthetic_error: bool) -> dict[str, Any]:
    with _temporary_env("ENGRAM_WRAP_UP_MODE", "standard"):
        standard = await run_diagnostic(
            live_closeout=False,
            live_inspect=False,
            synthetic_error=synthetic_error,
        )
    with _temporary_env("ENGRAM_WRAP_UP_MODE", "fast"):
        fast = await run_diagnostic(
            live_closeout=False,
            live_inspect=False,
            synthetic_error=synthetic_error,
        )
    return {
        "schema": "wrap_up_session_compare.v1",
        "live_store": False,
        "standard": standard,
        "fast": fast,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON payload.")
    parser.add_argument("--live-inspect", action="store_true", help="Inspect live store metadata without writing.")
    parser.add_argument("--live-closeout", action="store_true", help="Run a writeful live closeout diagnostic.")
    parser.add_argument("--allow-write", action="store_true", help="Required with --live-closeout.")
    parser.add_argument("--synthetic-error", action="store_true", help="Inject a sanitized synthetic path error.")
    parser.add_argument("--compare-fast", action="store_true", help="Compare isolated standard and fast closeout.")
    args = parser.parse_args()

    if args.live_closeout and not args.allow_write:
        print("--live-closeout requires --allow-write", file=sys.stderr)
        return 2
    if args.live_inspect and args.live_closeout:
        print("choose only one live mode", file=sys.stderr)
        return 2
    if args.compare_fast and (args.live_inspect or args.live_closeout):
        print("--compare-fast only runs isolated diagnostics", file=sys.stderr)
        return 2

    if args.compare_fast:
        payload = asyncio.run(run_compare_fast(synthetic_error=bool(args.synthetic_error)))
    else:
        payload = asyncio.run(run_diagnostic(
            live_closeout=bool(args.live_closeout),
            live_inspect=bool(args.live_inspect),
            synthetic_error=bool(args.synthetic_error),
        ))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("wrap_up_session diagnostic")
        if payload.get("schema") == "wrap_up_session_compare.v1":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"store_mode={payload['store_mode']} "
                f"live_store={payload['live_store']} "
                f"writeful={payload['writeful']}"
            )
            print(json.dumps(payload["timing"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
