from types import SimpleNamespace


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
