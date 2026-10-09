"""A closed synthetic transport must not contaminate the next test."""
import asyncio
from types import SimpleNamespace


def test_first_transport_sets_closed_state(monkeypatch):
    from piia_engram import mcp_server as server
    monkeypatch.setattr(server, "_parse_args", lambda: SimpleNamespace(transport="stdio", host="127.0.0.1", port=8123))
    monkeypatch.setattr(server, "_configure_utf8_stdio", lambda: None)
    monkeypatch.setattr(server, "_run_startup_auto_migrate", lambda: None)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: None)
    monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
    server.main()
    assert server._shutting_down is True


def test_next_test_can_write_to_its_new_store(tmp_path, monkeypatch):
    from piia_engram import Engram, mcp_server as server
    assert server._shutting_down is False
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(server, "_engram", eng)
    result = asyncio.run(server.add_lesson(summary="Keep transport tests isolated", user_confirmed=True))
    assert "transport_unavailable" not in result
    assert any(r["summary"] == "Keep transport tests isolated" for r in eng.get_lessons(_update_access=False))
