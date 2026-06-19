"""Dock GUI Build 2 — the SPA shell serving.

Backend smoke per Codex's testing split: the `/` route is session-gated (no
session -> a friendly re-run page, not raw 401), the SPA references its assets,
and /static serves the JS/CSS. The rendered UI / interactions are verified via the
Chrome MCP / visual check, not pytest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from piia_engram.core import Engram
from piia_engram.dock_ui.app import create_app

_TOKEN = "spa-token"
_PORT = 8731
_BASE = f"http://127.0.0.1:{_PORT}"


@pytest.fixture()
def eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


@pytest.fixture()
def client(eng: Engram) -> TestClient:
    return TestClient(create_app(eng, auth_token=_TOKEN, port=_PORT), base_url=_BASE)


class TestDockSpaServing:
    def test_root_without_session_shows_friendly_reauth_page(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "serve --ui" in resp.text  # tells the owner how to recover
        assert resp.headers.get("cache-control") == "no-store"

    def test_root_with_session_serves_spa(self, client: TestClient):
        client.post("/auth/exchange", json={"token": _TOKEN})
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "/static/app.js" in resp.text
        assert "/static/app.css" in resp.text
        assert resp.headers.get("cache-control") == "no-store"

    def test_static_assets_served_on_loopback(self, client: TestClient):
        # JS/CSS carry no data, so they load Host-gated (no session needed).
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/app.css").status_code == 200

    def test_static_blocked_off_loopback(self, eng: Engram):
        c = TestClient(create_app(eng, auth_token=_TOKEN, port=_PORT),
                       base_url=f"http://evil.test:{_PORT}")
        assert c.get("/static/app.js").status_code == 403
