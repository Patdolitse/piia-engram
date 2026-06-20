"""Dock GUI (Build 1) — the `engram serve --ui` launcher (dock_ui.server).

The side effects (uvicorn.run / webbrowser.open) are injected so the wiring is
tested without binding a real socket or opening a browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("starlette")  # Dock GUI HTTP tests need the [ui] extra
from starlette.testclient import TestClient

# Load setup_wizard first so the pre-existing setup_wizard<->cli_commands import
# cycle resolves in the real CLI order before any test imports cli_commands.
import piia_engram.setup_wizard  # noqa: F401,E402
from piia_engram.core import Engram
from piia_engram.dock_ui import server as srv


@pytest.fixture()
def eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def test_make_token_is_high_entropy_and_unique():
    a, b = srv.make_token(), srv.make_token()
    assert len(a) >= 24 and a != b


def test_launch_url_puts_token_in_fragment():
    assert srv.launch_url(8731, "abc") == "http://127.0.0.1:8731/auth#t=abc"


def test_serve_ui_token_in_url_authenticates_the_served_app(eng: Engram):
    captured: dict = {}
    srv.serve_ui(
        engram=eng, port=8731,
        _runner=lambda app, **kw: captured.update(app=app, kw=kw),
        _opener=lambda url: captured.update(url=url),
    )
    # the browser is opened to the auth bootstrap with the token in the fragment
    assert "/auth#t=" in captured["url"]
    token = captured["url"].split("#t=", 1)[1]
    assert token

    # the served app actually accepts that exact token...
    client = TestClient(captured["app"], base_url="http://127.0.0.1:8731")
    assert client.post("/auth/exchange", json={"token": token}).status_code == 200
    # ...and only once (single-use), proving the token is real, not a stub.
    client2 = TestClient(captured["app"], base_url="http://127.0.0.1:8731")
    assert client2.post("/auth/exchange", json={"token": token}).status_code == 401


def test_serve_ui_binds_loopback_only(eng: Engram):
    captured: dict = {}
    srv.serve_ui(
        engram=eng, port=8731,
        _runner=lambda app, **kw: captured.update(kw),
        _opener=lambda url: None,
    )
    assert captured.get("host") == "127.0.0.1"
    assert captured.get("port") == 8731


def test_run_serve_ui_invokes_launcher(monkeypatch):
    from piia_engram import cli_commands
    from piia_engram.dock_ui import server as srv_mod

    calls: dict = {}
    monkeypatch.setattr(srv_mod, "serve_ui", lambda **kw: calls.update(kw))
    rc = cli_commands._run_serve(["--ui", "--port", "8765"])
    assert rc == 0
    assert calls.get("port") == 8765


def test_run_serve_without_ui_shows_usage(capsys):
    from piia_engram import cli_commands

    rc = cli_commands._run_serve([])
    assert rc != 0
    assert "serve --ui" in capsys.readouterr().out
