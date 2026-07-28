"""Engram MCP Server.

Exposes Engram as an MCP server over stdio or SSE transport.
Any MCP-compatible AI tool can access the user's identity, preferences,
lessons, decisions, and skills.

Usage:
    python mcp_server.py
    python -m piia_engram.mcp_server --transport sse

Designed for local stdio transport and self-hosted remote SSE transport.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_write_operation_lock = threading.RLock()


def _configure_utf8_stdio() -> None:
    """Force UTF-8 stdio for MCP JSON frames on Windows-style consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not reconfigure:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (TypeError, ValueError, OSError):
            pass


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _locked_engram_call(fn, *args, **kwargs):
    """Serialize MCP write operations that may read-modify-write JSON stores."""
    with _write_operation_lock:
        return fn(*args, **kwargs)


def _startup_sync_mode(is_ephemeral: bool) -> str:
    """Return startup reconcile mode: background (default), eager, or off."""
    if is_ephemeral:
        return "off"

    raw = os.environ.get("ENGRAM_MCP_STARTUP_SYNC", "").strip().lower()
    if not raw:
        return "background"
    if raw in ("background", "bg", "async", "lazy", "on", "1", "true", "yes"):
        return "background"
    if raw in ("eager", "sync"):
        return "eager"
    if raw in ("off", "0", "false", "no", "none", "disabled"):
        return "off"

    logger.warning(
        "invalid ENGRAM_MCP_STARTUP_SYNC=%r; using background startup sync",
        raw,
    )
    return "background"


def _run_startup_auto_migrate() -> None:
    """Run stdio startup migration without writing to stdout."""
    try:
        from piia_engram.setup_wizard import auto_migrate  # type: ignore[import]
    except ImportError:
        try:
            from setup_wizard import auto_migrate  # type: ignore[import]
        except ImportError:
            auto_migrate = None  # type: ignore[assignment]
    if auto_migrate is not None:
        auto_migrate()


def _run_startup_sync() -> None:
    """Reconcile external AI memories/configs on MCP startup."""
    if _engram is None:
        return
    try:
        with _write_operation_lock:
            _mem = _engram.reconcile_memories()
            _cfg = _engram.reconcile_ai_configs()
        if _mem["imported"] or _cfg["imported"]:
            _msgs = []
            if _mem["imported"]:
                _msgs.append(f"memories={_mem['imported']}")
            if _cfg["imported"]:
                _msgs.append(f"configs={_cfg['imported']}")
            print(
                f"[engram] startup sync: {', '.join(_msgs)}",
                file=sys.stderr,
            )
    except Exception as exc:
        logger.warning("startup sync failed: %s", exc)


def _schedule_startup_sync(mode: str) -> None:
    if mode == "off":
        return
    if mode == "eager":
        _run_startup_sync()
        return

    thread = threading.Thread(
        target=_run_startup_sync,
        name="engram-startup-sync",
        daemon=True,
    )
    thread.start()


from piia_engram.beta_tracker import track_event as _beta
from piia_engram import provenance as _provenance
from piia_engram.continuity_digest import build_session_digest as _build_session_digest

# Starlette imports are deferred to SSE mode — not needed for stdio.
# Importing eagerly can slow startup and fail in minimal Docker images.
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    _HAS_STARLETTE = True
except ImportError:
    _HAS_STARLETTE = False

# ---------------------------------------------------------------------------
# Sibling import setup (same pattern as local_llm_bridge.py)
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402
try:
    from .core import (  # noqa: E402
        Engram,
        _project_id,
        export_to_openclaw,
        import_from_openclaw,
        strip_untrusted_trust_fields,
    )
except ImportError:
    from core import (  # noqa: E402
        Engram,
        _project_id,
        export_to_openclaw,
        import_from_openclaw,
        strip_untrusted_trust_fields,
    )
try:
    from . import governance_runtime as _gov_rt  # noqa: E402
except ImportError:
    import governance_runtime as _gov_rt  # noqa: E402
try:
    from . import recall_service as _recall_service  # noqa: E402
except ImportError:
    import recall_service as _recall_service  # noqa: E402
try:
    from . import context_governance as _context_governance  # noqa: E402
except ImportError:
    import context_governance as _context_governance  # noqa: E402
try:
    from .tool_surface import (  # noqa: E402
        ALL_CAPABILITY_TOOLS,
        CAPABILITY_GROUPS,
        CAPABILITY_MODE_NAMES,
        TIER1_TOOLS,
        resolve_capability_mode_details as _resolve_capability_mode_details,
    )
except ImportError:
    from tool_surface import (  # type: ignore[no-redef]  # noqa: E402
        ALL_CAPABILITY_TOOLS,
        CAPABILITY_GROUPS,
        CAPABILITY_MODE_NAMES,
        TIER1_TOOLS,
        resolve_capability_mode_details as _resolve_capability_mode_details,
    )

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
def _argv_requests_help(
    argv: list[str] | None = None,
    program: str | None = None,
) -> bool:
    args = sys.argv[1:] if argv is None else argv
    if not any(arg in {"-h", "--help"} for arg in args):
        return False
    executable = Path(sys.argv[0] if program is None else program).name.lower()
    return executable in {"mcp_server.py", "piia-engram-mcp", "piia-engram-mcp.exe"}


def _init_engram(root: Path | None = None) -> tuple[Engram | None, str | None]:
    """Create an Engram instance, returning (instance, None) on success or
    (None, error_message) if the store is corrupted / unreadable."""
    try:
        return Engram(root=root) if root else Engram(), None
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Engram init failed — MCP server running in degraded mode: %s", msg,
        )
        return None, msg


__SENTINEL = object()


def _require_engram(
    _engram: Engram | None | object = __SENTINEL,
    _init_error: str | None | object = __SENTINEL,
) -> Engram:
    """Return the live Engram instance or raise a clear error in degraded mode.

    When called with no arguments, reads the module-level globals.  Tests can
    pass explicit values for isolation.
    """
    import piia_engram.mcp_server as _self
    eng = _self._engram if _engram is __SENTINEL else _engram
    err = _self._init_error if _init_error is __SENTINEL else _init_error
    if eng is not None:
        return eng  # type: ignore[return-value]
    detail = err or "unknown error"
    raise RuntimeError(
        f"Engram is running in degraded mode — all tools are unavailable. "
        f"Original error: {detail}. "
        f"Run 'piia-engram-mcp doctor' or check your store directory."
    )


def _get_engram() -> Engram:
    """Shorthand for ``_require_engram()`` — used by tool handlers."""
    return _require_engram()


# argparse help should be a read-only, side-effect-free path. Initializing
# Engram here can emit data-fragmentation warnings or touch session files
# before argparse exits, so defer it only for the MCP help entrypoint.
if _argv_requests_help():
    _engram: Engram | None = None
    _init_error: str | None = None
else:
    _engram, _init_error = _init_engram()

# Anonymous usage statistics tracker (Phase 1: local log only)
try:
    from .telemetry import ToolCallTracker as _ToolCallTracker
except ImportError:
    try:
        from telemetry import ToolCallTracker as _ToolCallTracker  # type: ignore
    except ImportError:
        _ToolCallTracker = None  # type: ignore

_tracker = _ToolCallTracker() if _ToolCallTracker else None
_track_count = 0  # count calls for periodic flush
_FLUSH_EVERY = 10  # flush every N tool calls to avoid data loss


def _flush_telemetry(force: bool = False) -> None:
    """Flush telemetry data. Called periodically and on exit."""
    if _tracker is None:
        return
    try:
        from importlib.metadata import version as _pkg_version
        try:
            _ver = _pkg_version("piia-engram")
        except Exception:
            _ver = "dev"
        _tier = os.environ.get("ENGRAM_TOOLS", "core")
        _tracker.flush(engram_version=_ver, tools_tier=_tier, force=force)
    except Exception:
        pass  # never let telemetry affect MCP tools


# ---------------------------------------------------------------------------
# Session auto-tracking: record tool calls, auto-save on exit
# ---------------------------------------------------------------------------
from datetime import datetime as _dt


class _SessionTracker:
    """Track tool calls during this MCP server session.

    On process exit, automatically saves the accumulated operation log
    via save_agent_context so sessions are never lost — even when the
    AI tool forgets to call save_agent_context explicitly.
    """

    # Tools that indicate cold-start, not real work
    _COLD_START_TOOLS = frozenset({
        "get_user_context", "refresh_quick_context", "get_identity_card",
    })
    # Minimum non-cold-start calls to trigger auto-save
    _MIN_CALLS = 2

    _CHECKPOINT_EVERY = 20  # interim save every N real tool calls
    _DEFAULT_HEARTBEAT_INTERVAL = 300  # seconds — every 5 minutes by default
    # Floor for heartbeat interval to prevent runaway CPU/IO (mostly for tests).
    _MIN_HEARTBEAT_INTERVAL = 1

    def __init__(self) -> None:
        self.session_id = f"auto-{_dt.now().strftime('%Y-%m-%dT%H-%M-%S')}"
        self.start_time = _dt.now()
        self.tool_name: str = ""        # detected connecting tool
        self.client_info: dict[str, str] = {}  # MCP clientInfo from initialize
        self.project_folder: str = ""   # detected project path
        self.calls: list[dict[str, str]] = []
        self.saved = False
        self._real_call_count = 0       # non-cold-start call counter
        self._checkpoint_seq = 0        # checkpoint sequence number
        # Time-based heartbeat (v3.30 mechanism 2):
        # Background daemon thread saves a checkpoint every
        # ENGRAM_HEARTBEAT_INTERVAL seconds (default 300) when there is
        # unsaved activity. This guarantees crash-mid-session loses at most
        # one interval's worth of progress, even if the process dies before
        # atexit fires (SIGKILL, OOM, host crash). Set the env var to 0 to
        # disable. _lock serializes record / _interim_save across threads.
        self._lock = threading.Lock()
        # _last_save_at sentinel: datetime.min means "never saved".
        self._last_save_at = _dt.min
        # Timestamp of the most recent record() call (kept for diagnostics).
        self._last_activity_at = self.start_time
        # Monotonic activity / save sequences. The heartbeat uses these
        # — not timestamps — to detect "new activity since last save".
        # Timestamps hit Windows' ~15ms clock precision when record() and
        # _interim_save_locked() run back-to-back inside the same tick,
        # which made the H6 deterministic tests flaky. A monotonic int
        # never collides.
        self._activity_seq = 0
        self._saved_seq = 0
        self._heartbeat_interval = self._read_heartbeat_interval()
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        if self._heartbeat_interval > 0:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="engram-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    @staticmethod
    def _read_heartbeat_interval() -> int:
        """Parse ENGRAM_HEARTBEAT_INTERVAL env (seconds). 0 disables, default 300."""
        raw = os.environ.get("ENGRAM_HEARTBEAT_INTERVAL")
        if raw is None or raw.strip() == "":
            return _SessionTracker._DEFAULT_HEARTBEAT_INTERVAL
        try:
            v = int(raw.strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid ENGRAM_HEARTBEAT_INTERVAL=%r; using default %ds",
                raw, _SessionTracker._DEFAULT_HEARTBEAT_INTERVAL,
            )
            return _SessionTracker._DEFAULT_HEARTBEAT_INTERVAL
        if v <= 0:
            return 0  # disabled
        return max(v, _SessionTracker._MIN_HEARTBEAT_INTERVAL)

    def record(self, tool_called: str, args_summary: str = "") -> None:
        with self._lock:
            now = _dt.now()
            self.calls.append({
                "tool_called": tool_called,
                "timestamp": now.strftime("%H:%M:%S"),
                "args_summary": args_summary,
            })
            self._last_activity_at = now
            self._activity_seq += 1
            trigger_checkpoint = False
            if tool_called not in self._COLD_START_TOOLS:
                self._real_call_count += 1
                if (self._real_call_count % self._CHECKPOINT_EVERY == 0
                        and self._real_call_count > 0):
                    trigger_checkpoint = True
            if trigger_checkpoint:
                # Hold the lock through the save to keep counter and
                # _last_save_at consistent with the snapshot contents.
                self._interim_save_locked(reason="count")

    def _heartbeat_tick(self) -> bool:
        """Run one heartbeat iteration synchronously.

        Returns ``True`` if a checkpoint save was **attempted** this tick
        (i.e. there was unsaved activity and the save path was entered).
        The return value does NOT distinguish success from failure —
        ``_interim_save_locked`` is best-effort and swallows exceptions.
        Use ``_saved_seq == _activity_seq`` to check whether the most
        recent save actually landed.

        Extracted from ``_heartbeat_loop`` so tests can drive a single
        deterministic tick without ``time.sleep`` waits (H6). The
        decision uses the ``_activity_seq`` / ``_saved_seq`` counters
        rather than timestamps so back-to-back ticks on Windows' ~15ms
        clock can't false-positive "nothing new".
        """
        try:
            with self._lock:
                if not self.calls:
                    return False
                if self._saved_seq >= self._activity_seq:
                    return False
                self._interim_save_locked(reason="heartbeat")
                return True
        except Exception as exc:
            logger.debug("heartbeat checkpoint failed: %s", exc)
            return False

    def _heartbeat_loop(self) -> None:
        """Daemon thread: periodically checkpoint when there is unsaved activity.

        Wakes every ``_heartbeat_interval`` seconds and calls
        ``_heartbeat_tick``. Stops cleanly when ``_stop_event`` is set
        (auto_save) or when the process is killed (daemon thread dies
        with the main thread).
        """
        interval = self._heartbeat_interval
        if interval <= 0:
            return
        while not self._stop_event.wait(interval):
            self._heartbeat_tick()

    def detect_tool(self, tool: str) -> None:
        if not self.tool_name and tool:
            self.tool_name = tool

    def detect_client_info(self, name: str, version: str = "") -> None:
        """Record the MCP clientInfo from the initialize handshake.

        Called once from the first tool invocation that has access to
        the FastMCP context.  Sets ``tool_name`` as a side-effect when
        the AI tool hasn't been detected yet (e.g. short sessions that
        never call save_agent_context).
        """
        if self.client_info:
            return  # already detected
        self.client_info = {"name": name, "version": version}
        logger.info("MCP client: %s %s", name, version)
        # Auto-map well-known MCP client names to our tool_name taxonomy
        # so session tracking works even if the AI never calls
        # save_agent_context(tool=...).
        _CLIENT_NAME_MAP = {
            "claude-code": "claude_code",
            "claude-desktop": "claude_desktop",
            "claude": "claude_desktop",  # older Claude Desktop builds
            "cursor": "cursor",
            "codex": "codex",
            "windsurf": "windsurf",
            "cline": "cline",
            "roo-code": "roo_code",
            "copilot": "copilot_vscode",
            "amazon-q": "amazon_q",
        }
        mapped = _CLIENT_NAME_MAP.get(name.lower(), name.lower().replace("-", "_"))
        self.detect_tool(mapped)

    def detect_project(self, folder: str) -> None:
        if not self.project_folder and folder:
            self.project_folder = folder

    def _interim_save(self) -> None:
        """Save a mid-session checkpoint without marking session as done.

        Thin wrapper that acquires the lock. Internal callers that already
        hold the lock (record, heartbeat loop) should call
        _interim_save_locked() directly.
        """
        with self._lock:
            self._interim_save_locked(reason="manual")

    def _interim_save_locked(self, reason: str = "count") -> None:
        """Same as _interim_save but the caller must hold self._lock.

        Args:
            reason: short tag included in the checkpoint header so we can
                tell apart call-count vs time-heartbeat vs manual saves
                when auditing ~/.engram/contexts/. Values: ``count`` (every
                CHECKPOINT_EVERY calls), ``heartbeat`` (time-based), or
                ``manual``.
        """
        self._checkpoint_seq += 1
        tool = self.tool_name or "mcp_auto"
        duration = max(1, int((_dt.now() - self.start_time).total_seconds() / 60))

        seen: dict[str, None] = {}
        for c in self.calls:
            seen.setdefault(c["tool_called"], None)

        client_label = (
            f"{self.client_info['name']} {self.client_info.get('version', '')}"
            if self.client_info else ""
        )
        content = (
            f"[中间检查点 #{self._checkpoint_seq}] 触发: {reason} · "
            f"会话时长: {duration} 分钟\n"
            f"工具调用次数: {len(self.calls)}\n"
            f"使用的工具: {', '.join(seen.keys())}\n"
        )
        if client_label:
            content += f"MCP 客户端: {client_label}\n"
        actions = [
            {
                "tool_called": c["tool_called"],
                "arguments_summary": c.get("args_summary", ""),
                "result_summary": "",
            }
            for c in self.calls[-30:]
        ]
        try:
            _locked_engram_call(
                _engram.save_agent_context,
                tool=tool,
                content=content,
                session_id=f"{self.session_id}-cp{self._checkpoint_seq}",
                project_folder=self.project_folder,
                actions=actions,
            )
            # Only mark "saved" if the call succeeded — otherwise the
            # heartbeat will retry on its next tick. ``_saved_seq``
            # snapshots ``_activity_seq`` at the start of this save so a
            # record() that lands after the I/O but before this line
            # still triggers another heartbeat tick.
            self._last_save_at = _dt.now()
            self._saved_seq = self._activity_seq
        except Exception:
            pass  # silent — checkpoints are best-effort

    def auto_save(self) -> None:
        """Save accumulated session log. Called by atexit handler."""
        # Stop the heartbeat thread first so it doesn't race the final save.
        # M5: join the thread to guarantee it isn't mid-write when we
        # proceed with the final checkpoint below.
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=3.0)
            if self._heartbeat_thread.is_alive():
                logger.warning(
                    "heartbeat thread still alive after 3 s join; "
                    "final save may overlap — proceeding anyway"
                )
        if self.saved:
            return
        # Check minimum work threshold
        real_calls = [
            c for c in self.calls
            if c["tool_called"] not in self._COLD_START_TOOLS
        ]
        if len(real_calls) < self._MIN_CALLS:
            return
        self.saved = True

        # M2 fix: acquire the same lock the heartbeat thread uses so
        # that even if join(timeout=3.0) expired and the thread is
        # still alive, we serialize rather than race the final save.
        with self._lock:
            tool = self.tool_name or "mcp_auto"
            duration = max(1, int((_dt.now() - self.start_time).total_seconds() / 60))

            # Deduplicated tool list preserving order
            seen: dict[str, None] = {}
            for c in self.calls:
                seen.setdefault(c["tool_called"], None)
            unique_tools = list(seen.keys())

            client_label = (
                f"{self.client_info['name']} {self.client_info.get('version', '')}"
                if self.client_info else ""
            )
            content = (
                f"[MCP 自动记录] 会话时长: {duration} 分钟\n"
                f"工具调用次数: {len(self.calls)}\n"
                f"使用的工具: {', '.join(unique_tools)}\n"
            )
            if client_label:
                content += f"MCP 客户端: {client_label}\n"

            # Keep last 50 actions to prevent oversized files
            actions = [
                {
                    "tool_called": c["tool_called"],
                    "arguments_summary": c.get("args_summary", ""),
                    "result_summary": "",
                }
                for c in self.calls[-50:]
            ]

            try:
                _locked_engram_call(
                    _engram.save_agent_context,
                    tool=tool,
                    content=content,
                    session_id=self.session_id,
                    project_folder=self.project_folder,
                    actions=actions,
                )
            except Exception as exc:
                logger.warning("session auto-save failed: %s", exc)

        # Auto-update project snapshot with current metrics
        if self.project_folder:
            try:
                project_info = _collect_project_info(self.project_folder)
                if project_info:
                    verified_at = _dt.now().isoformat()
                    project_info["last_auto_snapshot"] = verified_at
                    _locked_engram_call(
                        _engram.save_project_snapshot,
                        self.project_folder,
                        project_info,
                    )
            except Exception as exc:
                logger.warning("project snapshot auto-update failed: %s", exc)


def _collect_project_info(project_folder: str) -> dict:
    """Collect lightweight project metrics from the filesystem.

    Returns a dict suitable for save_project_snapshot() merge.
    Returns empty dict if project_folder is invalid or not a Python project.
    Safe: no exceptions raised, no heavy deps, no blocking I/O.
    """
    if not project_folder:
        return {}

    root = Path(project_folder)
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return {}

    info: dict = {}

    # 1. Version from pyproject.toml
    try:
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            info["version"] = m.group(1)
    except Exception:
        pass

    # 2. Module count: .py files in src/ (excluding __pycache__)
    try:
        src_dir = root / "src"
        if src_dir.is_dir():
            info["module_count"] = sum(
                1 for p in src_dir.rglob("*.py")
                if "__pycache__" not in str(p)
            )
    except Exception:
        pass

    # 3. Test count: def test_ functions in tests/
    try:
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            tc = 0
            for tf in tests_dir.rglob("*.py"):
                if "__pycache__" in str(tf):
                    continue
                try:
                    for line in tf.read_text(encoding="utf-8").splitlines():
                        s = line.lstrip()
                        if s.startswith("def test_") or s.startswith("async def test_"):
                            tc += 1
                except Exception:
                    continue
            info["test_count"] = tc
    except Exception:
        pass

    # 4. MCP tool count: @mcp.tool() decorators
    try:
        for pkg_dir in (root / "src").iterdir():
            server_py = pkg_dir / "mcp_server.py"
            if server_py.is_file():
                info["mcp_tool_definitions"] = server_py.read_text(
                    encoding="utf-8",
                ).count("@mcp.tool()")
                break
    except Exception:
        pass

    # 5. Local git position: useful for cross-tool handoff, never required.
    try:
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        if head.returncode == 0 and head.stdout.strip():
            info["latest_local_commit"] = head.stdout.strip()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        if branch.returncode == 0 and branch.stdout.strip():
            info["current_branch"] = branch.stdout.strip()
    except Exception:
        pass

    return info


def _session_handoff_state(
    summary: str,
    *,
    source_tool: str = "",
    project_folder: str = "",
    session_ref: str = "",
) -> dict:
    """Extract only explicit completion/next/blocking signals for a checkpoint."""
    digest = _build_session_digest(
        summary,
        tool=source_tool,
        project_id=_project_id(project_folder) if project_folder else "",
        session_ref=session_ref,
    )
    completed = [str(item) for item in digest.get("completed") or [] if str(item).strip()]
    next_actions = [str(item) for item in digest.get("next_actions") or [] if str(item).strip()]
    blocked_on = [
        str(item)
        for item in digest.get("risks") or []
        if any(marker in str(item).lower() for marker in ("block", "blocked", "阻塞"))
    ]
    handoff: dict[str, object] = {
        "last_completed": completed[:8],
        "next_actions": next_actions[:8],
        "blocked_on": blocked_on[:5],
        "session_status": (
            "completed"
            if completed
            else "interrupted"
            if next_actions or blocked_on
            else "unknown"
        ),
    }
    if next_actions:
        handoff["current_focus"] = next_actions[0]
    return handoff


def _attach_current_state(
    info: dict,
    *,
    verified_at: str | None = None,
    handoff: dict | None = None,
) -> dict:
    """Attach machine-derived project facts without discarding legacy fields."""
    if not isinstance(info, dict):
        return {}
    out = dict(info)
    keys = (
        "version",
        "module_count",
        "test_count",
        "mcp_tool_definitions",
        "latest_local_commit",
        "current_branch",
    )
    current = {key: out[key] for key in keys if out.get(key) is not None}
    if isinstance(handoff, dict):
        current.update(handoff)
    if current:
        current["verified_at"] = verified_at or _dt.now().isoformat()
        out["current_state"] = current
    return out


_session = _SessionTracker()


def _engram_clean_shutdown() -> None:
    """v3.30 mechanism (1): auto-save + telemetry flush + mark clean exit.

    Runs at atexit. The session_state.json mark is what doctor reads to
    decide whether to surface "previous session ended unexpectedly". If
    this function runs to completion, the next Engram() init will read
    last_clean_exit=True and not warn.
    """
    try:
        _session.auto_save()
    except Exception:
        pass
    try:
        _flush_telemetry(force=True)
    except Exception:
        pass
    try:
        if _engram is not None:
            _engram._mark_clean_exit(last_session_id=_session.session_id)
    except Exception:
        pass


# Register atexit handler: auto-save session, flush telemetry, mark clean exit
import atexit
atexit.register(_engram_clean_shutdown)


def _detect_mcp_client_once() -> None:
    """Extract clientInfo from the MCP session on the first tool call.

    Called from ``_track`` so every tool handler automatically triggers
    it — no need to add ``ctx`` parameters to individual tools.
    Safe to call outside a request context (returns silently).
    """
    if _session.client_info:
        return  # already detected
    try:
        ctx = mcp.get_context()
        session = ctx.session
        params = session.client_params
        if params and params.clientInfo:
            _session.detect_client_info(
                params.clientInfo.name,
                params.clientInfo.version,
            )
    except Exception:
        pass  # not in a request context, or client didn't send info


def _track(tool_name: str, success: bool = True, args_summary: str = "") -> None:
    """Record a tool call for telemetry and session auto-tracking.

    Flushes every _FLUSH_EVERY calls to avoid losing data when the
    MCP server process is killed without a clean wrap_up_session.
    """
    global _track_count
    _detect_mcp_client_once()
    # Governance read paths must be disk-side-effect free for non-owners.
    # _tracker.flush() and _session checkpoints both persist files, so suppress
    # tracking for read-classed tools unless the caller is the owner. Governance
    # OFF still behaves exactly as before because caller_is_owner() returns True.
    tool_class = globals().get("TOOL_GOVERNANCE_CLASS", {}).get(tool_name)
    if tool_class == "read":
        try:
            if not _gov_rt.caller_is_owner(_engram.root):
                return
        except Exception:
            try:
                if _gov_rt.governance_enabled():
                    return
            except Exception:
                return
    # Governance write-boundary: a DENIED low-trust mutating write must be
    # disk-side-effect free too. Without this, a refused non-owner write still
    # falls through to the telemetry flush + session checkpoint below; if the
    # global session counter happens to hit _CHECKPOINT_EVERY on that call, a
    # context autosave (contexts/<client>/auto-*-cpN.md) is written ON BEHALF of
    # a caller who was refused — breaking the "refused mutating tool writes no
    # data file" contract the WriterSpy guards. Mirrors the read-class
    # suppression above for the write-refusal case; suppressing here drops both
    # the telemetry flush and the session checkpoint, and prevents the denied
    # call's args_summary from ever reaching a checkpoint. Owner failures and
    # governance-OFF still record exactly as before (caller_is_owner() is True).
    if success is False and tool_class in WRITE_GATE_CLASSES_MUTATING:
        try:
            if not _gov_rt.caller_is_owner(_engram.root):
                return
        except Exception:
            try:
                if _gov_rt.governance_enabled():
                    return
            except Exception:
                return
    if _tracker is not None:
        _tracker.record(tool_name, success=success)
        _track_count += 1
        if _track_count >= _FLUSH_EVERY:
            _track_count = 0
            _flush_telemetry(force=True)
    # Session auto-tracking
    _session.record(tool_name, args_summary)


def _track_read_safe(tool_name: str, success: bool = True, args_summary: str = "") -> None:
    """``_track`` variant for read-semantics branches inside WRITE-classed tools.

    ``_track`` only suppresses non-owner disk side effects (telemetry flush /
    session checkpoints) for tools whose governance class is "read". The v4
    merged tools (``playbook_execution``, ``user_portrait``, ``review_staging``)
    are write-classed but contain pure read branches; those branches call this
    helper so a non-owner read attempt stays disk-side-effect free, replicating
    the read-class suppression locally. Fail-closed: if the owner check itself
    fails, tracking is skipped whenever governance is (or may be) enabled.
    """
    _detect_mcp_client_once()
    try:
        if not _gov_rt.caller_is_owner(_engram.root):
            return
    except Exception:
        try:
            if _gov_rt.governance_enabled():
                return
        except Exception:
            return
    _track(tool_name, success=success, args_summary=args_summary)


IDENTITY_FIELDS = frozenset({
    "profile",
    "preferences",
    "trust_boundaries",
    "work_style",
    "quality_standards",
})

TOOL_TIER = os.environ.get("ENGRAM_TOOLS", "core").strip() or "core"


def _warn_unknown_capability_tokens(
    unknown_tokens: list[str],
    *,
    fallback_to_core: bool,
) -> None:
    legal = ", ".join(sorted(CAPABILITY_MODE_NAMES))
    unknown = ", ".join(unknown_tokens)
    suffix = ""
    if fallback_to_core:
        suffix = (
            " All tokens unknown; falling back to core. / "
            "全部 token 未知，回落 core。"
        )
    print(
        "[engram] WARNING: Unknown ENGRAM_TOOLS token(s): "
        f"{unknown}. Legal values / 合法值: {legal}. "
        f"Ignoring unknown token(s). / 已忽略未知 token。{suffix}",
        file=sys.stderr,
    )


def resolve_capability_modes(raw: str | None) -> frozenset[str]:
    """Resolve ENGRAM_TOOLS capability modes to the retained tool-name set."""
    details = _resolve_capability_mode_details(raw)
    unknown = list(details["unknown_tokens"])
    if unknown:
        _warn_unknown_capability_tokens(
            unknown,
            fallback_to_core=bool(details["fallback_to_core"]),
        )
    return details["tools"]  # type: ignore[return-value]

# ---------------------------------------------------------------------------
# Write-gate governance matrix — DENY BY DEFAULT (Codex round-5 a4 hardening)
# ---------------------------------------------------------------------------
# Every ``@mcp.tool()`` MUST be classified here. ``test_write_gate_matrix.py``
# reflects over all registered tools and FAILS if any tool is missing — so a
# newly added tool cannot ship un-triaged. The behavioural counterpart
# (``test_write_gate_matrix.py`` writer-spy) asserts that, under
# ``ENGRAM_GOVERNANCE=1`` + a low-trust client, every WRITE-class tool refuses
# and produces zero file delta. Together they make "forgot to gate a new write
# tool" a red build rather than a silent low-trust write hole.
#
# Categories:
#   "read"              — no store mutation; read-path governance (if any) is
#                         handled by the a0/a1-a3 read gates, not here.
#   "governed_write"    — mutates the knowledge store; gated by
#                         ``maybe_refuse_write`` (verified + direct_write may
#                         write; read-only-external is refused).
#   "owner_only_write"  — mutates the GRANT STORE itself or imports/overwrites
#                         the whole store; gated by ``maybe_refuse_owner_write``
#                         (private-self only) BEFORE any side effect.
#   "export_owner_only" — writes a full-knowledge dump / report FILE to disk;
#                         gated by ``maybe_refuse_export`` (private-self only).
#   "safe_allowlist"    — explicitly reviewed as needing no write gate despite
#                         not being a pure reader (currently unused).
WRITE_GATE_CLASSES = frozenset(
    {"read", "governed_write", "owner_only_write", "export_owner_only", "safe_allowlist"}
)
WRITE_GATE_CLASSES_MUTATING = frozenset(
    {"governed_write", "owner_only_write", "export_owner_only"}
)
TOOL_GOVERNANCE_CLASS: dict[str, str] = {
    # --- owner_only_write: grant store / whole-store import ---
    "manage_caller_trust": "owner_only_write",
    "import_engram": "owner_only_write",
    "confirm_knowledge": "owner_only_write",
    "onboard_repo": "owner_only_write",
    "onboard_accept": "owner_only_write",
    "check_anchors": "owner_only_write",
    # --- export_owner_only: full dump / report file to disk ---
    "export_engram": "export_owner_only",
    "export_knowledge_report": "export_owner_only",
    "request_outline_review": "export_owner_only",
    "refresh_quick_context": "export_owner_only",
    # --- governed_write: ordinary knowledge-store mutations ---
    "memory_store": "governed_write",
    "add_lesson": "governed_write",
    "add_decision": "governed_write",
    "add_playbook": "governed_write",
    "ingest_notes": "governed_write",
    "extract_session_insights": "governed_write",
    "update_knowledge": "governed_write",
    "archive_knowledge": "governed_write",
    "review_staging": "governed_write",
    "merge_knowledge": "governed_write",
    "manage_relation": "governed_write",
    "update_identity": "governed_write",
    "save_project_snapshot": "governed_write",
    "user_portrait": "governed_write",
    "start_project": "governed_write",
    "save_agent_context": "governed_write",
    "register_tool": "governed_write",
    "manage_playbook": "governed_write",
    # playbook_execution mixes semantics per action: the declared class is the
    # unconditional first gate (maybe_refuse_write); the prepare branch
    # additionally gates maybe_refuse_export BEFORE the plan file is written,
    # and the status branch keeps its owner-only result gate.
    "playbook_execution": "governed_write",
    "wrap_up_session": "governed_write",
    # --- read: no store mutation ---
    "doctor": "read",
    "export_feedback_report": "read",  # counts/distributions only, no bodies
    "find_tool": "read",
    "get_audit_log": "read",
    "get_daily_log": "read",
    "get_decisions": "read",
    "explore_knowledge": "read",
    "get_wrap_up_session_status": "read",
    # export/file-writer: embeds lesson summaries + decision text verbatim AND
    # writes exports/identity_card.md to disk; gated by maybe_refuse_export.
    # Classed export_owner_only so the writer-spy matrix verifies that gate
    # (Codex round-6: was mislabeled "read", leaving its export gate untested).
    "get_identity_card": "export_owner_only",
    "get_identity_facets": "read",
    "get_knowledge_inheritance": "read",
    "get_knowledge_overview": "read",
    "get_lessons": "read",
    "get_permission_profile": "read",
    "preview_context_governance": "read",
    "get_playbooks": "read",
    "get_project_context": "read",
    "get_recall": "read",
    "get_recent_context": "read",
    "get_relevant_knowledge": "read",
    "get_resume_brief": "read",
    "get_stale_knowledge": "read",
    "get_user_context": "read",
    "list_agent_sessions": "read",
    "list_projects": "read",
    "list_tools": "read",
    "read_web_content": "read",
    "search_knowledge": "read",
}

mcp = FastMCP(
    "engram",
    instructions=(
        "Engram — the user's personal memory layer across all AI tools.\n\n"
        "Memory lifecycle (act on each phase without waiting for the user to ask):\n\n"
        "1. STARTUP  — get_user_context: inject user identity & context at conversation start.\n"
        "2. RETRIEVAL — search_knowledge / get_relevant_knowledge: look up past knowledge mid-conversation.\n"
        "3. WRITEBACK — memory_store (or add_lesson / add_decision / add_playbook): persist new knowledge.\n"
        "4. SESSION END — wrap_up_session: save session context & sync.\n\n"
        "Quick reference:\n"
        "- Conversation start → get_user_context(level='standard')\n"
        "- Need past knowledge → search_knowledge(query, filters_json='{\"tier\":\"verified\"}')\n"
        "- Learned something reusable → memory_store(kind='lesson', content_json=...)\n"
        "- Decision made → memory_store(kind='decision', content_json=...)\n"
        "- Conversation end → wrap_up_session\n"
    ),
)


def _apply_tool_tier() -> None:
    """Filter registered MCP tools according to ENGRAM_TOOLS capability modes."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return

    retained_tools = resolve_capability_modes(TOOL_TIER)
    for name in list(tools):
        if name in retained_tools:
            continue
        try:
            mcp.remove_tool(name)
        except Exception:
            tools.pop(name, None)


def _json(obj: object) -> str:
    """Serialize to JSON string, handling empty results."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _attach_provenance(
    entry: dict,
    *,
    source_agent: str = "",
    run_id: str = "",
    last_validated_at: str = "",
) -> None:
    """Merge normalized provenance fields into ``entry['provenance']`` in place.

    Additive only (Provenance & Freshness Contract v1, follow-up A): malformed or
    empty values are dropped by ``normalize_provenance_fields`` so a write is
    never blocked, and when nothing valid is supplied the entry is left
    byte-identical to before (no empty ``provenance`` key is created).
    """
    prov = _provenance.normalize_provenance_fields(
        {
            "source_agent": source_agent,
            "run_id": run_id,
            "last_validated_at": last_validated_at,
        }
    )
    if not prov:
        return
    existing = entry.get("provenance")
    if isinstance(existing, dict):
        merged = dict(existing)
        merged.update(prov)
        entry["provenance"] = merged
    else:
        entry["provenance"] = prov


def _user_lang() -> str:
    """Detect user language from profile. Returns 'zh' or 'en'."""
    from piia_engram.i18n import get_lang
    return get_lang()


def _validate_path(value: str, *, allow_empty: bool = False) -> str | None:
    """Light path hygiene for user-supplied filesystem paths.

    Engram is a local-first tool — the calling user already has full disk
    access, so this is NOT a sandboxing boundary. It only rejects the small
    set of inputs that silently break downstream system calls:

    - **Null bytes (\\x00)** — cause silent truncation in many C-level path
      APIs; treated as an attack signature.
    - **Empty / whitespace-only** — usually a programming bug, surface it loudly.

    Returns ``None`` when the value is valid; otherwise returns a human-readable
    error string the tool can return verbatim to the caller.
    """
    if value is None:
        return None if allow_empty else "路径参数缺失"
    if not isinstance(value, str):
        return f"路径参数必须是字符串（收到 {type(value).__name__}）"
    if "\x00" in value:
        return "路径包含 NUL 字节（不允许）"
    if not allow_empty and not value.strip():
        return "路径不能为空"
    return None


def _safe_err(exc: Exception) -> str:
    """Return a sanitized error message without internal filesystem paths."""
    msg = str(exc)
    # Strip any Windows/Unix absolute paths from the message
    msg = re.sub(r'\\\\[^\\\r\n]+\\[^\\\r\n]+(?:\\[^\\\r\n]+)*', '<path>', msg)
    msg = re.sub(r'[A-Za-z]:\\[^\\\r\n]+(?:\\[^\\\r\n]+)*', '<path>', msg)
    msg = re.sub(r'/[\w/. -]{3,}', '<path>', msg)
    return msg


def _format_permissions_section(perms: dict) -> str:
    """Render a caller-permissions dict as a Markdown section for cold-start.

    Appended to ``get_user_context`` output (a1) so the consuming AI tool
    knows its governance status and trust boundary from the first message.
    """
    lines = ["\n\n## Caller Permissions / 调用方权限"]
    if not perms.get("governance_enabled"):
        lines.append(
            "- Governance: **disabled** — all tools and data are accessible. "
            "治理层未启用，所有工具和数据均可访问。"
        )
        return "\n".join(lines)

    lines.append("- Governance: **enabled** / 治理层已启用")
    aid = perms.get("agent_id", "unknown")
    trust = perms.get("trust_level", "unknown")
    lines.append(f"- Identity / 身份: `{aid}` → trust level `{trust}`")
    lines.append(f"- Access ceiling / 访问天花板: `{perms.get('max_sensitivity', '?')}`")
    lines.append(f"- Write policy / 写入策略: `{perms.get('write_policy', '?')}`")
    if perms.get("revoked"):
        lines.append("- ⚠ Your access has been **revoked** by the user. / 你的访问权限已被用户撤销。")
    if perms.get("grant_error"):
        lines.append(f"- Grant resolution error: {perms['grant_error']}")
    note = perms.get("note", "")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for transport configuration."""
    parser = argparse.ArgumentParser(description="Engram MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode: stdio (local) or sse (remote). Default: stdio.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (sse mode only). Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8767,
        help="Port to bind (sse mode only). Default: 8767.",
    )
    return parser.parse_args(argv[1:] if argv is not None else None)


if _HAS_STARLETTE:
    class TokenAuthMiddleware(BaseHTTPMiddleware):
        """Simple Bearer token auth for remote SSE mode."""

        def __init__(self, app, token: str):
            super().__init__(app)
            self.token = token

        async def dispatch(self, request: Request, call_next):
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer ") or not secrets.compare_digest(auth_header[7:], self.token):
                return JSONResponse(
                    {"error": "Unauthorized. Set ENGRAM_AUTH_TOKEN."},
                    status_code=401,
                )
            return await call_next(request)


# ===========================================================================
# READ TOOLS (19)
# ===========================================================================

# Cold-start playbook trigger matching scans at most this many playbooks
# (newest index entries first) so a large library can't slow down startup.
_PLAYBOOK_MATCH_SCAN_LIMIT = 200



@mcp.resource("engram://identity/profile")
def resource_profile() -> str:
    """用户身份画像（已按信任边界过滤）。"""
    return _json(_engram.get_safe_profile())


@mcp.resource("engram://identity/preferences")
def resource_preferences() -> str:
    """用户工作偏好（v2.0）。"""
    return _json(_engram.get_preferences())


@mcp.resource("engram://identity/trust-boundaries")
def resource_trust_boundaries() -> str:
    """数据信任边界。"""
    return _json(_engram.get_trust_boundaries())


@mcp.resource("engram://identity/work-style")
def resource_work_style() -> str:
    """用户工作偏好（v1兼容）。"""
    return _json(_engram.get_work_style())


@mcp.resource("engram://identity/quality-standards")
def resource_quality_standards() -> str:
    """用户质量标准。"""
    return _json(_engram.get_quality_standards())


@mcp.resource("engram://knowledge/domains")
def resource_domains() -> str:
    """用户技术领域经验图谱。"""
    return _json(_engram.get_domains())


@mcp.resource("engram://stats")
def resource_stats() -> str:
    """知识资产统计。"""
    return _json(_engram.get_stats())


# ===========================================================================
# AGENT CONTEXT RECOVERY (3)
# ===========================================================================


# Tool implementations live in mcp_tools_* modules, which bind back to this
# module via late attribute lookup (S.<name>) so monkeypatches keep working.
# When run as a script / `python -m`, this module is `__main__`; alias it so
# the children import THIS instance instead of re-importing the file.
if __name__ == "__main__":
    sys.modules.setdefault("piia_engram.mcp_server", sys.modules[__name__])
    sys.modules.setdefault("mcp_server", sys.modules[__name__])

try:
    from .mcp_tools_read import (  # noqa: E402,F401 — re-exports
        get_user_context,
        refresh_quick_context,
        get_identity_card,
        get_identity_facets,
        get_lessons,
        get_decisions,
        get_project_context,
        list_projects,
        get_relevant_knowledge,
        get_knowledge_inheritance,
        search_knowledge,
        get_knowledge_overview,
        explore_knowledge,
        export_knowledge_report,
    )
    from .mcp_tools_write import (  # noqa: E402,F401 — re-exports
        memory_store,
        add_lesson,
        add_decision,
        add_playbook,
        _PLAYBOOK_USAGE_POLICY,
        _EXECUTION_USAGE_POLICY,
        _inject_usage_policy,
        get_playbooks,
        manage_playbook,
        playbook_execution,
        register_tool,
        find_tool,
        list_tools,
    )
    from .mcp_tools_knowledge import (  # noqa: E402,F401 — re-exports
        ingest_notes,
        extract_session_insights,
        update_knowledge,
        archive_knowledge,
        confirm_knowledge,
        onboard_repo,
        onboard_accept,
        check_anchors,
        review_staging,
        get_stale_knowledge,
        request_outline_review,
        merge_knowledge,
        manage_relation,
    )
    from .mcp_tools_admin import (  # noqa: E402,F401 — re-exports
        get_permission_profile,
        manage_caller_trust,
        update_identity,
        save_project_snapshot,
        user_portrait,
        read_web_content,
        export_engram,
        import_engram,
        get_audit_log,
        wrap_up_session,
        get_wrap_up_session_status,
        export_feedback_report,
        doctor,
        start_project,
    )
    from .mcp_tools_session import (  # noqa: E402,F401 — re-exports
        save_agent_context,
        get_recent_context,
        list_agent_sessions,
        get_resume_brief,
        get_recall,
        preview_context_governance,
        get_daily_log,
    )
except ImportError:  # plain-script mode (no package context)
    from mcp_tools_read import (  # noqa: E402,F401 — re-exports
        get_user_context,
        refresh_quick_context,
        get_identity_card,
        get_identity_facets,
        get_lessons,
        get_decisions,
        get_project_context,
        list_projects,
        get_relevant_knowledge,
        get_knowledge_inheritance,
        search_knowledge,
        get_knowledge_overview,
        explore_knowledge,
        export_knowledge_report,
    )
    from mcp_tools_write import (  # noqa: E402,F401 — re-exports
        memory_store,
        add_lesson,
        add_decision,
        add_playbook,
        _PLAYBOOK_USAGE_POLICY,
        _EXECUTION_USAGE_POLICY,
        _inject_usage_policy,
        get_playbooks,
        manage_playbook,
        playbook_execution,
        register_tool,
        find_tool,
        list_tools,
    )
    from mcp_tools_knowledge import (  # noqa: E402,F401 — re-exports
        ingest_notes,
        extract_session_insights,
        update_knowledge,
        archive_knowledge,
        confirm_knowledge,
        onboard_repo,
        onboard_accept,
        check_anchors,
        review_staging,
        get_stale_knowledge,
        request_outline_review,
        merge_knowledge,
        manage_relation,
    )
    from mcp_tools_admin import (  # noqa: E402,F401 — re-exports
        get_permission_profile,
        manage_caller_trust,
        update_identity,
        save_project_snapshot,
        user_portrait,
        read_web_content,
        export_engram,
        import_engram,
        get_audit_log,
        wrap_up_session,
        get_wrap_up_session_status,
        export_feedback_report,
        doctor,
        start_project,
    )
    from mcp_tools_session import (  # noqa: E402,F401 — re-exports
        save_agent_context,
        get_recent_context,
        list_agent_sessions,
        get_resume_brief,
        get_recall,
        preview_context_governance,
        get_daily_log,
    )

_apply_tool_tier()


def main() -> None:
    """Console entry point for the Engram MCP server.

    Exposed as the ``piia-engram-mcp`` console script so any MCP client can
    launch the server with a single command — e.g. ``uvx piia-engram-mcp``
    (zero pre-install) or a plain ``piia-engram-mcp`` in the client config.
    Equivalent to ``python -m piia_engram.mcp_server``; both paths call here.
    """
    _configure_utf8_stdio()
    args = _parse_args()

    # ── Startup self-check: detect stale invocation paths ──
    _invoked_via = sys.argv[0] if sys.argv else ""
    if "engram_core" in _invoked_via:
        print(
            "[engram] WARNING: Invoked via deprecated path 'engram_core'. "
            "Update your MCP config to use 'piia_engram.mcp_server'. "
            "Run 'engram doctor --fix' to auto-repair.",
            file=sys.stderr,
        )

    # Detect ephemeral/Docker environments where no local AI tools exist.
    # Skip auto_migrate and reconcile to speed up startup (critical for
    # mcp-proxy which has short connection timeouts).
    _is_ephemeral = os.path.isfile("/.dockerenv") or _env_flag_enabled("ENGRAM_EPHEMERAL")

    # Auto-migrate legacy configs on first run after upgrade (stdio only;
    # must happen before mcp.run() to avoid polluting the MCP stdio channel).
    if args.transport == "stdio" and not _is_ephemeral:
        _run_startup_auto_migrate()

    # Auto-reconcile on MCP server startup — runs once regardless of which
    # AI tool connects.  This ensures cross-tool memory sync happens even if
    # the AI tool never calls get_user_context.
    # Skip in ephemeral containers — no AI tool configs to scan.
    # Startup sync policy: background by default, eager/off by env override.
    _schedule_startup_sync(_startup_sync_mode(_is_ephemeral))

    if args.transport == "sse":
        if not _HAS_STARLETTE:
            print("ERROR: SSE mode requires starlette. Install: pip install piia-engram[remote]")
            sys.exit(1)
        token = os.environ.get("ENGRAM_AUTH_TOKEN", "").strip()
        if not token:
            print("ERROR: ENGRAM_AUTH_TOKEN environment variable is required for SSE mode.")
            print(
                'Generate one with: python -c "import secrets; '
                'print(secrets.token_urlsafe(32))"'
            )
            sys.exit(1)

        mcp.settings.host = args.host
        mcp.settings.port = args.port

        if args.host == "0.0.0.0":
            print(
                "WARNING: Binding to 0.0.0.0 exposes Engram to the network. "
                "Use HTTPS (nginx/caddy) in production.",
                file=sys.stderr,
            )

        allowed_origins = os.environ.get("ENGRAM_CORS_ORIGINS", "").strip()

        print(f"Engram MCP server (SSE) on http://{args.host}:{args.port}/sse")

        starlette_app = mcp.sse_app()
        starlette_app.add_middleware(TokenAuthMiddleware, token=token)

        if allowed_origins:
            from starlette.middleware.cors import CORSMiddleware
            starlette_app.add_middleware(
                CORSMiddleware,
                allow_origins=[o.strip() for o in allowed_origins.split(",")],
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization"],
            )

        import uvicorn
        uvicorn.run(starlette_app, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
