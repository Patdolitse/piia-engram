"""Dock GUI Build 2 — the SPA shell serving.

Backend smoke per Codex's testing split: the `/` route is session-gated (no
session -> a friendly re-run page, not raw 401), the SPA references its assets,
and /static serves the JS/CSS. The rendered UI / interactions are verified via the
Chrome MCP / visual check, not pytest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("starlette")  # Dock GUI HTTP tests need the [ui] extra
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


def test_all_static_files_are_covered_by_package_data():
    """Release guard: every file under dock_ui/static must match a pyproject
    package-data glob, or `pip install piia-engram[ui]` would ship a GUI missing
    assets (the wheel only includes declared package data). Reads the actual globs
    from pyproject so adding e.g. a .svg without updating packaging trips this."""
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib

    import piia_engram.dock_ui as dock_ui

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["piia_engram.dock_ui"]
    covered = {g.rsplit(".", 1)[-1] for g in globs if g.startswith("static/")}

    static_dir = Path(dock_ui.__file__).parent / "static"
    stray = [
        p.name for p in static_dir.iterdir()
        if p.is_file() and p.suffix.lstrip(".") not in covered
    ]
    assert not stray, f"static files not covered by package-data globs {globs}: {stray}"
