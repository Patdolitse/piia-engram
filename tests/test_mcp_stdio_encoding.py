import asyncio
import os
import subprocess
import sys
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


def test_startup_sync_and_write_tool_share_write_lock(monkeypatch):
    from piia_engram import mcp_server

    events = []

    class FakeLock:
        def __enter__(self):
            events.append("lock:enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("lock:exit")

    monkeypatch.setattr(mcp_server, "_write_operation_lock", FakeLock())
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda: events.append("cfg") or {"imported": 0})

    mcp_server._run_startup_sync()

    assert events == ["lock:enter", "mem", "cfg", "lock:exit"]

    events.clear()
    monkeypatch.setattr(mcp_server._gov_rt, "maybe_refuse_write", lambda root, tool: None)
    monkeypatch.setattr(mcp_server._engram, "add_lesson", lambda lesson: events.append("add") or {"id": "lesson-1"})
    monkeypatch.setattr(mcp_server, "_track", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server, "_beta", lambda *args, **kwargs: None)

    result = asyncio.run(mcp_server.add_lesson("locked write", user_confirmed=True))

    assert result == "[Engram] 教训已记录 · tier=staging · 可召回: locked write"
    assert events == ["lock:enter", "add", "lock:exit"]


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
