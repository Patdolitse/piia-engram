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
