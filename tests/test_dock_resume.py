"""Tests for the zero-write resume entry (``engram dock-resume``).

A local desktop client must be able to read a resume brief without mutating the
store. These pin two things: (1) ``Engram(read_only=True)`` makes ZERO writes to
the store root (no session stamp / audit / migration / structure creation), and
(2) the ``dock-resume`` CLI emits a paste-ready brief / JSON and honors
``ENGRAM_DIR``. The read_only flag is additive — the normal init path is asserted
unchanged so existing behavior can't regress.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Suite isolation: never touch the real ~/.engram, keep audit/fragmentation
    # noise out of temp roots (same carve-out the rest of the suite uses).
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            body = p.read_bytes()
            out[str(p.relative_to(root))] = (
                p.stat().st_mtime_ns,
                hashlib.sha256(body).hexdigest(),
            )
    return out


def _populate(root: Path):
    """A normally-initialized store (structure + session stamp written)."""
    from piia_engram.core import Engram

    return Engram(root=root)


# --- core read_only flag: guaranteed zero writes ---------------------------


def test_read_only_open_makes_zero_writes(tmp_path):
    from piia_engram.core import Engram

    store = tmp_path / "store"
    _populate(store)  # normal init writes structure + session_state
    before = _snapshot(store)

    eng = Engram(root=store, read_only=True)
    brief = eng.get_resume_brief(project_folder="", token_budget=800)

    assert _snapshot(store) == before  # not one byte changed
    assert eng._read_only is True
    assert isinstance(brief, dict) and "markdown" in brief


def test_read_only_does_not_create_missing_store(tmp_path):
    from piia_engram.core import Engram

    store = tmp_path / "absent"
    Engram(root=store, read_only=True)  # opening read-only must not mkdir
    assert not store.exists()


def test_default_init_still_writes_structure(tmp_path):
    """Guard: read_only is additive — the normal path is unchanged."""
    from piia_engram.core import Engram

    store = tmp_path / "normal"
    Engram(root=store)
    assert (store / "schema_version.json").exists()
    assert (store / "session_state.json").exists()


# --- dock-resume CLI --------------------------------------------------------


def _cli(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_resume

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_resume(argv)


def test_dock_resume_help(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-resume" in capsys.readouterr().out


def test_dock_resume_text_is_paste_ready(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli(monkeypatch, tmp_path, []) == 0
    assert capsys.readouterr().out.strip()  # non-empty markdown body


def test_dock_resume_json_is_structured_and_read_only(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["engram_dir"] == str(tmp_path / "store")
    assert "markdown" in payload


def test_dock_resume_cli_is_zero_write_end_to_end(monkeypatch, tmp_path):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    _cli(monkeypatch, tmp_path, ["--json"])
    assert _snapshot(store) == before  # the whole CLI path wrote nothing


def test_dock_resume_rejects_bad_budget(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--budget", "x"]) == 2
    assert "budget" in capsys.readouterr().out.lower()
