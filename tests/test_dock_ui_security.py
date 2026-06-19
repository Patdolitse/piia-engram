"""Dock GUI (Build 1) — local server security gate.

Design locked with Codex (2026-06-19, see engram-dock-gui-design.md). This is the
highest-risk piece: a local HTTP server sitting on the user's private memory store.
Other local web pages / processes must NEVER be able to read or (worse) write the
store by forging requests. These tests are the security contract — written FIRST,
before any route content or UI.

Slice 1 (this batch): the one-time-token → server-side session auth gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from piia_engram.core import Engram
from piia_engram.dock_ui.app import create_app

_TOKEN = "test-one-time-token-abc123"
_PORT = 8731
_BASE = f"http://127.0.0.1:{_PORT}"


@pytest.fixture()
def eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


@pytest.fixture()
def client(eng: Engram) -> TestClient:
    # base_url sets the Host header to the loopback origin so the Host allowlist
    # passes; cookies persist across requests on the same client (browser-like).
    app = create_app(eng, auth_token=_TOKEN, port=_PORT)
    return TestClient(app, base_url=_BASE)


class TestDockUiAuthGate:
    def test_unauthenticated_read_is_rejected(self, client: TestClient):
        resp = client.get("/api/dock-status")
        assert resp.status_code == 401

    def test_valid_token_exchange_sets_session_and_returns_csrf(self, client: TestClient):
        resp = client.post("/auth/exchange", json={"token": _TOKEN})
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "engram_dock_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie
        # CSRF synchronizer token returned once for the SPA to hold in memory.
        assert resp.json().get("csrf")

    def test_reused_one_time_token_is_rejected(self, client: TestClient):
        first = client.post("/auth/exchange", json={"token": _TOKEN})
        assert first.status_code == 200
        second = client.post("/auth/exchange", json={"token": _TOKEN})
        assert second.status_code == 401

    def test_bad_token_is_rejected(self, client: TestClient):
        resp = client.post("/auth/exchange", json={"token": "wrong-token"})
        assert resp.status_code == 401

    def test_authenticated_read_succeeds_after_exchange(self, client: TestClient):
        client.post("/auth/exchange", json={"token": _TOKEN})
        resp = client.get("/api/dock-status")
        assert resp.status_code == 200

    def test_all_api_responses_are_no_store(self, client: TestClient):
        client.post("/auth/exchange", json={"token": _TOKEN})
        resp = client.get("/api/dock-status")
        assert resp.headers.get("cache-control") == "no-store"
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


class TestDockUiHostGuard:
    """DNS-rebinding defense: only loopback Host headers are served. A public site
    that rebinds its hostname to 127.0.0.1 still sends its own Host -> rejected."""

    def test_foreign_host_rejected(self, eng: Engram):
        app = create_app(eng, auth_token=_TOKEN, port=_PORT)
        c = TestClient(app, base_url=f"http://evil.test:{_PORT}")
        assert c.get("/api/dock-status").status_code == 403

    def test_wrong_port_host_rejected(self, eng: Engram):
        app = create_app(eng, auth_token=_TOKEN, port=_PORT)
        c = TestClient(app, base_url="http://127.0.0.1:9999")
        assert c.get("/api/dock-status").status_code == 403

    def test_loopback_host_allowed(self, eng: Engram):
        # localhost is allowlisted -> Host passes; still 401 because no session.
        app = create_app(eng, auth_token=_TOKEN, port=_PORT)
        c = TestClient(app, base_url=f"http://localhost:{_PORT}")
        assert c.get("/api/dock-status").status_code == 401

    def test_foreign_host_blocked_before_auth_exchange(self, eng: Engram):
        # Host guard runs before the token exchange — a foreign page can't even
        # attempt to spend the token.
        app = create_app(eng, auth_token=_TOKEN, port=_PORT)
        c = TestClient(app, base_url=f"http://evil.test:{_PORT}")
        assert c.post("/auth/exchange", json={"token": _TOKEN}).status_code == 403


class TestDockUiAuthPage:
    """The /auth bootstrap: the launcher opens /auth#t=<token>; the page reads the
    token from the URL FRAGMENT (never sent to the server / Referer), exchanges it,
    then strips it from history."""

    def test_auth_page_security_headers(self, client: TestClient):
        resp = client.get("/auth")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/html")
        assert resp.headers.get("cache-control") == "no-store"
        assert resp.headers.get("referrer-policy") == "no-referrer"

    def test_auth_page_bootstraps_fragment_exchange(self, client: TestClient):
        body = client.get("/auth").text
        assert "location.hash" in body      # read token from the fragment
        assert "/auth/exchange" in body     # POST it to exchange for a session
        assert "replaceState" in body       # strip the token from history
