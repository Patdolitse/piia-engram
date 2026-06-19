"""Dock GUI (Build 1) — write path: owner-gated reversible archive via HTTP.

Authorization model locked with Codex (Option A): the HTTP auth (one-time token ->
server-side session + Host allowlist + Origin + CSRF) IS the owner gate. A request
that fails auth must be rejected BEFORE any writable Engram is opened (zero
side-effect — same invariant we hardened in 4.7.0). The CLI dock-archive and this
HTTP route share one core (dock_ui.contracts.archive_entry).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from piia_engram.core import Engram
from piia_engram.dock_ui.app import create_app

_TOKEN = "test-one-time-token-write"
_PORT = 8731
_BASE = f"http://127.0.0.1:{_PORT}"


def _snap(root: Path) -> dict:
    out: dict[str, str] = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture()
def eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


@pytest.fixture()
def client(eng: Engram) -> TestClient:
    return TestClient(create_app(eng, auth_token=_TOKEN, port=_PORT), base_url=_BASE)


def _authed(client: TestClient) -> str:
    r = client.post("/auth/exchange", json={"token": _TOKEN})
    assert r.status_code == 200
    return r.json()["csrf"]


class TestDockArchiveWritePath:
    def test_archive_without_session_is_zero_side_effect(self, client: TestClient, eng: Engram):
        before = _snap(eng.root)
        resp = client.post("/api/dock-archive", json={"id": "anything"})
        assert resp.status_code == 401
        assert _snap(eng.root) == before  # no writable Engram opened, no write

    def test_archive_without_csrf_is_zero_side_effect(self, client: TestClient, eng: Engram):
        _authed(client)
        before = _snap(eng.root)
        resp = client.post("/api/dock-archive", json={"id": "anything"}, headers={"Origin": _BASE})
        assert resp.status_code == 403
        assert _snap(eng.root) == before

    def test_archive_bad_origin_is_zero_side_effect(self, client: TestClient, eng: Engram):
        csrf = _authed(client)
        before = _snap(eng.root)
        resp = client.post(
            "/api/dock-archive", json={"id": "anything"},
            headers={"Origin": "http://evil.test", "X-Engram-CSRF": csrf},
        )
        assert resp.status_code == 403
        assert _snap(eng.root) == before

    def test_archive_localhost_origin_rejected(self, client: TestClient):
        # Build 1 launcher uses 127.0.0.1; a localhost Origin is not the expected origin.
        csrf = _authed(client)
        resp = client.post(
            "/api/dock-archive", json={"id": "anything"},
            headers={"Origin": f"http://localhost:{_PORT}", "X-Engram-CSRF": csrf},
        )
        assert resp.status_code == 403

    def test_archive_happy_path_archives_item(self, client: TestClient, eng: Engram):
        rec = eng.add_lesson({"summary": "archive me via dock http"})
        lesson_id = rec["id"]
        csrf = _authed(client)
        resp = client.post(
            "/api/dock-archive", json={"id": lesson_id},
            headers={"Origin": _BASE, "X-Engram-CSRF": csrf},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body.get("reversible") is True
        # the lesson is soft-archived -> its tier is now "archived" (reversible, not deleted)
        lessons = {l.get("id"): l for l in Engram(root=eng.root).get_lessons(limit=None)}
        assert lessons[lesson_id].get("tier") == "archived"

    def test_get_read_does_not_require_origin_or_csrf(self, client: TestClient):
        _authed(client)
        resp = client.get("/api/dock-status")  # only Host + session required for reads
        assert resp.status_code == 200


class TestDockUpdateWritePath:
    """Inline content edit — same owner gate as archive; field whitelist; a primary
    field may be edited but never blanked."""

    def test_update_without_session_is_zero_side_effect(self, client: TestClient, eng: Engram):
        before = _snap(eng.root)
        resp = client.post("/api/dock-update", json={"id": "x", "updates": {"summary": "new"}})
        assert resp.status_code == 401
        assert _snap(eng.root) == before

    def test_update_without_csrf_is_zero_side_effect(self, client: TestClient, eng: Engram):
        _authed(client)
        before = _snap(eng.root)
        resp = client.post(
            "/api/dock-update", json={"id": "x", "updates": {"summary": "new"}},
            headers={"Origin": _BASE},
        )
        assert resp.status_code == 403
        assert _snap(eng.root) == before

    def test_update_happy_path_edits_field(self, client: TestClient, eng: Engram):
        lid = eng.add_lesson({"summary": "old summary"})["id"]
        csrf = _authed(client)
        resp = client.post(
            "/api/dock-update", json={"id": lid, "updates": {"summary": "new summary"}},
            headers={"Origin": _BASE, "X-Engram-CSRF": csrf},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        updated = next(l for l in Engram(root=eng.root).get_lessons(limit=None) if l["id"] == lid)
        assert updated["summary"] == "new summary"

    def test_update_cannot_blank_a_primary_field(self, client: TestClient, eng: Engram):
        lid = eng.add_lesson({"summary": "keep me"})["id"]
        csrf = _authed(client)
        resp = client.post(
            "/api/dock-update", json={"id": lid, "updates": {"summary": "   "}},
            headers={"Origin": _BASE, "X-Engram-CSRF": csrf},
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
        kept = next(l for l in Engram(root=eng.root).get_lessons(limit=None) if l["id"] == lid)
        assert kept["summary"] == "keep me"  # original intact
