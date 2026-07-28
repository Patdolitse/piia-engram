import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent


class _FakeStream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_mcp_server_configures_stdio_to_utf8(monkeypatch):
    from piia_engram import mcp_server

    stdout = _FakeStream()
    stderr = _FakeStream()
    monkeypatch.setattr(mcp_server.sys, "stdout", stdout)
    monkeypatch.setattr(mcp_server.sys, "stderr", stderr)

    mcp_server._configure_utf8_stdio()

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_mcp_server_main_configures_stdio_before_run(monkeypatch):
    from piia_engram import mcp_server

    events = []

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: {"imported": 0})
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events[:2] == ["utf8", "run:stdio"]


def test_mcp_server_startup_sync_defaults_to_background(monkeypatch):
    from piia_engram import mcp_server

    events = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            events.append(f"thread:{name}:{daemon}")
            self._target = target

        def start(self):
            events.append("thread:start")
            self._target()

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    monkeypatch.delenv("ENGRAM_MCP_STARTUP_SYNC", raising=False)
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.threading, "Thread", FakeThread)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events == [
        "utf8",
        "migrate",
        "thread:engram-startup-sync:True",
        "thread:start",
        "mem",
        "cfg",
        "run:stdio",
    ]


def test_mcp_server_startup_sync_eager_runs_before_server(monkeypatch):
    from piia_engram import mcp_server

    events = []

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "eager")
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events == ["utf8", "migrate", "mem", "cfg", "run:stdio"]


def test_startup_sync_mode_truthy_aliases_keep_background(monkeypatch):
    from piia_engram import mcp_server

    for raw in ("1", "true", "yes", "on", "background", "bg", "async"):
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", raw)
        assert mcp_server._startup_sync_mode(is_ephemeral=False) == "background"

    for raw in ("eager", "sync"):
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", raw)
        assert mcp_server._startup_sync_mode(is_ephemeral=False) == "eager"

    for raw in ("off", "0", "false", "no", "none", "disabled"):
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", raw)
        assert mcp_server._startup_sync_mode(is_ephemeral=False) == "off"


def test_mcp_server_startup_sync_off_skips_reconcile(monkeypatch):
    from piia_engram import mcp_server

    events = []

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "off")
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events == ["utf8", "migrate", "run:stdio"]


def test_mcp_server_ephemeral_overrides_startup_sync(monkeypatch):
    from piia_engram import mcp_server

    events = []

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "eager")
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events == ["utf8", "run:stdio"]


def test_mcp_server_background_startup_sync_errors_do_not_crash(monkeypatch):
    from piia_engram import mcp_server

    events = []
    warnings = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self._target = target

        def start(self):
            events.append("thread:start")
            self._target()

    def boom():
        events.append("mem")
        raise RuntimeError("sync boom")

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    monkeypatch.delenv("ENGRAM_MCP_STARTUP_SYNC", raising=False)
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", boom)
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.threading, "Thread", FakeThread)
    monkeypatch.setattr(mcp_server.logger, "warning", lambda message, exc: warnings.append((message, str(exc))))
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

    mcp_server.main()

    assert events == ["utf8", "migrate", "thread:start", "mem", "run:stdio"]
    assert warnings == [("startup sync failed: %s", "sync boom")]


def test_startup_sync_does_not_block_normal_write_lock(monkeypatch):
    from piia_engram import mcp_server

    reconcile_started = threading.Event()
    allow_reconcile_finish = threading.Event()
    write_completed = threading.Event()

    def slow_reconcile():
        reconcile_started.set()
        assert allow_reconcile_finish.wait(timeout=2)
        return {"imported": 0}

    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", slow_reconcile)
    monkeypatch.setattr(
        mcp_server._engram,
        "reconcile_ai_configs",
        lambda: {"imported": 0},
    )

    startup_thread = threading.Thread(target=mcp_server._run_startup_sync)
    startup_thread.start()
    assert reconcile_started.wait(timeout=1)

    def run_normal_write():
        mcp_server._locked_engram_call(lambda: None)
        write_completed.set()

    write_thread = threading.Thread(target=run_normal_write)
    write_thread.start()

    try:
        assert write_completed.wait(timeout=0.5)
    finally:
        allow_reconcile_finish.set()
        write_thread.join(timeout=1)
        startup_thread.join(timeout=2)

    assert not startup_thread.is_alive()


def test_help_detection_only_applies_to_mcp_entrypoint():
    from piia_engram import mcp_server

    assert mcp_server._argv_requests_help(["--help"], "mcp_server.py") is True
    assert mcp_server._argv_requests_help(["--help"], "piia-engram-mcp.exe") is True
    assert mcp_server._argv_requests_help(["--help"], "pytest.exe") is False


def test_mcp_server_help_does_not_initialize_engram(tmp_path):
    home = tmp_path / "home"
    orphan = home / ".engram" / "knowledge"
    orphan.mkdir(parents=True)
    (orphan / "lessons.json").write_text("[]", encoding="utf-8")

    active_root = tmp_path / "active-root"
    env = os.environ.copy()
    env.update({
        "ENGRAM_DIR": str(active_root),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT / "src"),
    })
    env.pop("ENGRAM_TEST", None)

    result = subprocess.run(
        [sys.executable, "-m", "piia_engram.mcp_server", "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "usage:" in result.stdout
    assert "DATA FRAGMENTATION" not in output
    assert not active_root.exists()
