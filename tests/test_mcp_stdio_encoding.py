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


def _stub_start(monkeypatch, events, *, ephemeral=False):
    """main() with the transport stubbed and the import engine spied on."""
    from piia_engram import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "_parse_args",
        lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123),
    )
    if ephemeral:
        monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
    else:
        monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
    monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: events.append("utf8"))
    monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: events.append("migrate"))
    monkeypatch.setattr(mcp_server._engram, "reconcile_memories", lambda **kw: events.append("mem") or {"imported": 0})
    monkeypatch.setattr(mcp_server._engram, "reconcile_ai_configs", lambda **kw: events.append("cfg") or {"imported": 0})
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: events.append(f"run:{transport}"))
    return mcp_server


def test_mcp_server_start_imports_nothing_by_default(monkeypatch):
    # Earlier the start scheduled a background import of other AI tools'
    # memories; now only `engram import-memories` imports them.
    events = []
    monkeypatch.delenv("ENGRAM_MCP_STARTUP_SYNC", raising=False)
    mcp_server = _stub_start(monkeypatch, events)
    started = []
    real_thread = mcp_server.threading.Thread

    def spy_thread(*args, **kwargs):
        started.append(kwargs.get("name", ""))
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(mcp_server.threading, "Thread", spy_thread)

    mcp_server.main()

    assert events == ["utf8", "migrate", "run:stdio"]
    assert "engram-startup-sync" not in started


def test_mcp_server_startup_sync_eager_no_longer_imports(monkeypatch):
    events = []
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "eager")
    mcp_server = _stub_start(monkeypatch, events)

    mcp_server.main()

    assert events == ["utf8", "migrate", "run:stdio"]


def test_legacy_startup_sync_values_are_accepted_quietly(monkeypatch, capsys):
    # ENGRAM_MCP_STARTUP_SYNC stays accepted for old configs: no error, no
    # warning, and no import whatever the value.
    values = ("1", "true", "yes", "on", "background", "bg", "async", "eager", "sync",
              "off", "0", "false", "no", "none", "disabled", "not-a-mode")
    for raw in values:
        events = []
        monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", raw)
        mcp_server = _stub_start(monkeypatch, events)
        mcp_server.main()
        assert events == ["utf8", "migrate", "run:stdio"], raw
    assert "ENGRAM_MCP_STARTUP_SYNC" not in capsys.readouterr().err


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
