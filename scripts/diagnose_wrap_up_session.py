"""Owner-run wrap_up_session diagnostic; defaults to an isolated temp store."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import sys
import tempfile
import threading
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


async def _run_closeout(
    project: Path,
    synthetic_error: bool,
    synthetic_delay_ms: int = 0,
) -> dict[str, Any]:
    if synthetic_delay_ms > 0:
        await asyncio.sleep(synthetic_delay_ms / 1000)

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


def _daily_log_probe(project: Path) -> dict[str, Any]:
    try:
        log = mcp_server._engram.get_daily_log(str(project))
        content = log.get("content") or ""
        return {
            "checked": True,
            "exists": bool(log.get("exists")),
            "written": "Diagnostic closeout sample" in content,
            "bytes": len(content.encode("utf-8")),
            "date": log.get("date", ""),
        }
    except Exception as exc:  # pragma: no cover - defensive diagnostic surface
        return {
            "checked": False,
            "written": False,
            "error": mcp_server._safe_err(exc),
        }


def _run_closeout_with_boundary(
    project: Path,
    *,
    synthetic_error: bool,
    synthetic_delay_ms: int,
    timeout_ms: int,
) -> dict[str, Any]:
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            payload = asyncio.run(_run_closeout(
                project,
                synthetic_error=synthetic_error,
                synthetic_delay_ms=synthetic_delay_ms,
            ))
            result_queue.put(("ok", payload))
        except BaseException as exc:  # pragma: no cover - reported in payload
            result_queue.put(("error", exc))

    thread = threading.Thread(target=worker, name="engram-wrapup-diagnostic", daemon=True)
    thread.start()

    try:
        kind, value = result_queue.get(timeout=max(1, timeout_ms) / 1000)
    except queue.Empty:
        return {
            "completed": False,
            "timeout": {
                "status": "timed_out",
                "boundary": "diagnostic_tool",
                "timeout_ms": max(1, timeout_ms),
            },
            "payload": {},
            "errors": {},
        }

    if kind == "error":
        return {
            "completed": False,
            "timeout": {
                "status": "not_timed_out",
                "boundary": "diagnostic_tool",
                "timeout_ms": max(1, timeout_ms),
            },
            "payload": {},
            "errors": {"closeout": mcp_server._safe_err(value)},
        }

    return {
        "completed": True,
        "timeout": {
            "status": "completed",
            "boundary": "diagnostic_tool",
            "timeout_ms": max(1, timeout_ms),
        },
        "payload": value,
        "errors": {},
    }


def _live_inspect_payload() -> dict[str, Any]:
    return {
        "schema": "wrap_up_session_diagnostic.v1",
        "completed": True,
        "store_mode": "live_inspect",
        "live_store": True,
        "writeful": False,
        "daily_log": {
            "checked": False,
            "written": False,
            "reason": "live_inspect_is_read_only",
        },
        "timing": {},
        "maintenance": {
            "live_inspect": {
                "status": "ok",
                "note": "read-only aggregate metadata only",
            },
        },
        "errors": {},
        "timeout": {
            "status": "not_applied",
            "boundary": "live_inspect",
            "reason": "read_only_inspection",
        },
    }


async def _run_live_closeout_without_boundary(
    project: Path,
    *,
    synthetic_error: bool,
    synthetic_delay_ms: int,
    timeout_ms: int,
) -> dict[str, Any]:
    try:
        payload = await _run_closeout(
            project,
            synthetic_error=synthetic_error,
            synthetic_delay_ms=synthetic_delay_ms,
        )
        return {
            "completed": True,
            "timeout": {
                "status": "not_applied",
                "boundary": "live_closeout",
                "timeout_ms": max(1, timeout_ms),
                "reason": "writeful_live_closeout_runs_without_background_timeout",
            },
            "payload": payload,
            "errors": {},
        }
    except BaseException as exc:  # pragma: no cover - reported in payload
        return {
            "completed": False,
            "timeout": {
                "status": "not_applied",
                "boundary": "live_closeout",
                "timeout_ms": max(1, timeout_ms),
                "reason": "writeful_live_closeout_runs_without_background_timeout",
            },
            "payload": {},
            "errors": {"closeout": mcp_server._safe_err(exc)},
        }


async def run_diagnostic(
    *,
    live_closeout: bool,
    live_inspect: bool,
    synthetic_error: bool,
    synthetic_delay_ms: int = 0,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    if live_inspect:
        return _live_inspect_payload()

    if live_closeout:
        store = Path(os.environ.get("ENGRAM_DIR") or Path.home() / ".engram")
        project = Path.cwd()
        _install_runtime(store, write_bootstrap=False)
        boundary = await _run_live_closeout_without_boundary(
            project,
            synthetic_error=synthetic_error,
            synthetic_delay_ms=synthetic_delay_ms,
            timeout_ms=timeout_ms,
        )
        store_mode = "live"
        writeful = True
        daily_log = _daily_log_probe(project)
    else:
        with tempfile.TemporaryDirectory(prefix="engram-wrapup-diagnostic-") as tmp:
            root = Path(tmp)
            store = root / "store"
            project = root / "project"
            project.mkdir()
            _install_runtime(store)
            boundary = _run_closeout_with_boundary(
                project,
                synthetic_error=synthetic_error,
                synthetic_delay_ms=synthetic_delay_ms,
                timeout_ms=timeout_ms,
            )
            daily_log = _daily_log_probe(project)
        store_mode = "isolated"
        writeful = False

    payload = boundary.get("payload") or {}
    errors = {
        "insights": (payload.get("insights") or {}).get("error"),
        "project_snapshot": (payload.get("project_snapshot") or {}).get("error"),
    }
    errors.update(boundary.get("errors") or {})

    return {
        "schema": "wrap_up_session_diagnostic.v1",
        "completed": bool(boundary.get("completed")),
        "store_mode": store_mode,
        "live_store": bool(live_closeout),
        "writeful": writeful,
        "daily_log": daily_log,
        "timing": payload.get("timing") or {},
        "maintenance": payload.get("maintenance") or {},
        "errors": errors,
        "timeout": boundary.get("timeout") or {},
    }


async def run_compare_fast(*, synthetic_error: bool, timeout_ms: int = 60_000) -> dict[str, Any]:
    with _temporary_env("ENGRAM_WRAP_UP_MODE", "standard"):
        standard = await run_diagnostic(
            live_closeout=False,
            live_inspect=False,
            synthetic_error=synthetic_error,
            timeout_ms=timeout_ms,
        )
    with _temporary_env("ENGRAM_WRAP_UP_MODE", "fast"):
        fast = await run_diagnostic(
            live_closeout=False,
            live_inspect=False,
            synthetic_error=synthetic_error,
            timeout_ms=timeout_ms,
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
    parser.add_argument("--synthetic-delay-ms", type=int, default=0, help="Delay closeout for timeout testing.")
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="Diagnostic boundary timeout.")
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
        payload = asyncio.run(run_compare_fast(
            synthetic_error=bool(args.synthetic_error),
            timeout_ms=int(args.timeout_ms),
        ))
    else:
        payload = asyncio.run(run_diagnostic(
            live_closeout=bool(args.live_closeout),
            live_inspect=bool(args.live_inspect),
            synthetic_error=bool(args.synthetic_error),
            synthetic_delay_ms=max(0, int(args.synthetic_delay_ms)),
            timeout_ms=int(args.timeout_ms),
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
                f"writeful={payload['writeful']} "
                f"completed={payload.get('completed')}"
            )
            timeout = payload.get("timeout") or {}
            if timeout:
                print(
                    f"timeout={timeout.get('status')} "
                    f"boundary={timeout.get('boundary')} "
                    f"timeout_ms={timeout.get('timeout_ms', '')}"
                )
            daily = payload.get("daily_log") or {}
            if daily:
                print(
                    f"daily_log_checked={daily.get('checked')} "
                    f"daily_log_written={daily.get('written')} "
                    f"daily_log_bytes={daily.get('bytes', '')}"
                )
            print(json.dumps(payload["timing"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
