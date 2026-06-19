"""Dock GUI Build 2 — read routes: real memory data, session-gated, read_only
zero-write (the 4.7.0 discipline). The payload core is shared with the CLI.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from piia_engram.core import Engram
from piia_engram.dock_ui.app import create_app
from piia_engram.dock_ui.contracts import (
    dock_archived_list_payload,
    dock_memory_list_payload,
    dock_resume_payload,
)

_TOKEN = "read-token-xyz"
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
    e = Engram(root=tmp_path)
    e.add_lesson({"summary": "pin CI deps"})
    e.add_decision({"question": "ORM?", "choice": "SQLAlchemy"})
    return e


@pytest.fixture()
def client(eng: Engram) -> TestClient:
    return TestClient(create_app(eng, auth_token=_TOKEN, port=_PORT), base_url=_BASE)


def _authed(client: TestClient) -> str:
    return client.post("/auth/exchange", json={"token": _TOKEN}).json()["csrf"]


class TestDockMemoryCore:
    def test_lists_active_lessons_and_decisions(self, eng: Engram):
        payload = dock_memory_list_payload(Engram(root=eng.root, read_only=True))
        assert payload["ok"] is True
        assert payload["count"] == 2
        assert sorted(r["kind"] for r in payload["results"]) == ["decision", "lesson"]
        # editable fields are projected for the UI detail panel
        lesson = next(r for r in payload["results"] if r["kind"] == "lesson")
        assert lesson["fields"]["summary"] == "pin CI deps"

    def test_excludes_archived_entries(self, eng: Engram):
        lid = next(l["id"] for l in eng.get_lessons(limit=None))
        eng.soft_archive_knowledge_tier(lid, allow_verified=True)
        payload = dock_memory_list_payload(Engram(root=eng.root, read_only=True))
        assert [r["kind"] for r in payload["results"]] == ["decision"]

    def test_core_is_zero_write(self, eng: Engram, tmp_path: Path):
        before = _snap(tmp_path)
        dock_memory_list_payload(Engram(root=eng.root, read_only=True))
        assert _snap(tmp_path) == before


class TestDockMemoryRoute:
    def test_requires_session(self, client: TestClient):
        assert client.get("/api/dock-memory").status_code == 401

    def test_returns_active_memory(self, client: TestClient):
        _authed(client)
        resp = client.get("/api/dock-memory")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["count"] == 2
        assert resp.headers.get("cache-control") == "no-store"
        # the server's local path is NOT leaked to the browser
        assert "engram_dir" not in body

    def test_route_is_zero_write(self, client: TestClient, eng: Engram):
        _authed(client)
        before = _snap(eng.root)
        client.get("/api/dock-memory")
        assert _snap(eng.root) == before


class TestDockResumeCore:
    """接续 (the soul): a paste-ready cross-tool resume brief (the 智能标准包)."""

    def test_returns_ok_with_markdown_string(self, eng: Engram):
        payload = dock_resume_payload(Engram(root=eng.root, read_only=True))
        assert payload["ok"] is True
        assert isinstance(payload.get("markdown"), str)

    def test_core_is_zero_write(self, eng: Engram, tmp_path: Path):
        before = _snap(tmp_path)
        dock_resume_payload(Engram(root=eng.root, read_only=True))
        assert _snap(tmp_path) == before


class TestDockResumeRoute:
    def test_requires_session(self, client: TestClient):
        assert client.get("/api/dock-resume").status_code == 401

    def test_returns_brief(self, client: TestClient):
        _authed(client)
        resp = client.get("/api/dock-resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "markdown" in body
        assert resp.headers.get("cache-control") == "no-store"

    def test_route_is_zero_write(self, client: TestClient, eng: Engram):
        _authed(client)
        before = _snap(eng.root)
        client.get("/api/dock-resume")
        assert _snap(eng.root) == before


class TestDockArchivedCore:
    """回收站: a zero-write list of archived (soft-deleted) entries, so the GUI can
    offer one-click restore. Shares the core with the CLI `dock-archived`."""

    def test_lists_only_archived_entries(self, eng: Engram):
        lid = next(l["id"] for l in eng.get_lessons(limit=None))
        eng.soft_archive_knowledge_tier(lid, allow_verified=True)
        payload = dock_archived_list_payload(Engram(root=eng.root, read_only=True))
        assert payload["ok"] is True
        assert payload["count"] == 1  # only the archived lesson; the active decision is excluded
        row = payload["results"][0]
        assert row["id"] == lid and row["kind"] == "lesson" and row["title"]

    def test_empty_when_nothing_archived(self, eng: Engram):
        payload = dock_archived_list_payload(Engram(root=eng.root, read_only=True))
        assert payload["ok"] is True and payload["count"] == 0 and payload["results"] == []

    def test_core_is_zero_write(self, eng: Engram, tmp_path: Path):
        lid = next(l["id"] for l in eng.get_lessons(limit=None))
        eng.soft_archive_knowledge_tier(lid, allow_verified=True)
        before = _snap(tmp_path)
        dock_archived_list_payload(Engram(root=eng.root, read_only=True))
        assert _snap(tmp_path) == before


class TestDockArchivedRoute:
    def test_requires_session(self, client: TestClient):
        assert client.get("/api/dock-archived").status_code == 401

    def test_returns_archived(self, client: TestClient, eng: Engram):
        lid = next(l["id"] for l in eng.get_lessons(limit=None))
        eng.soft_archive_knowledge_tier(lid, allow_verified=True)
        _authed(client)
        resp = client.get("/api/dock-archived")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["count"] == 1
        assert resp.headers.get("cache-control") == "no-store"
        assert "engram_dir" not in body  # the server's local path is NOT leaked to the browser

    def test_route_is_zero_write(self, client: TestClient, eng: Engram):
        _authed(client)
        before = _snap(eng.root)
        client.get("/api/dock-archived")
        assert _snap(eng.root) == before
