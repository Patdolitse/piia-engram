"""Engram(read_only=True) never writes the store (4.21.1, plan amendment A6).

Every public Engram method is classified as a store write or read-only safe.
Write verbs on a read-only handle return {"error": "read_only"} and leave the
whole store root byte-identical; the write entry points raise
ReadOnlyStoreError as a backstop; no read path touches either.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from piia_engram import core
from piia_engram.core import Engram
from piia_engram.storage import ReadOnlyStoreError


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _public_methods() -> set[str]:
    return {n for n, v in inspect.getmembers(Engram) if not n.startswith("_") and callable(v)}


def _dummy_args(func) -> dict:
    kwargs = {}
    for name, param in inspect.signature(func).parameters.items():
        if name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        ann = str(param.annotation)
        if "dict" in ann:
            kwargs[name] = {"summary": "x", "title": "x", "steps": ["a"]}
        elif "list" in ann:
            kwargs[name] = []
        elif "int" in ann:
            kwargs[name] = 1
        elif "bool" in ann:
            kwargs[name] = False
        else:
            kwargs[name] = "x"
    return kwargs


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root)
    eng.add_lesson("seeded lesson for the read-only sweep", domain="t")
    eng.add_decision("seeded question?", "seeded choice", "seeded reason")
    eng.add_playbook({"title": "Seeded procedure", "steps": ["one", "two", "three"], "triggers": ["seed"]})
    eng.update_profile({"role": "developer"})
    return root


def test_every_public_method_is_classified():
    public = _public_methods()
    write, read = core.STORE_WRITE_METHODS, core.READ_ONLY_SAFE_METHODS
    assert not (write & read), f"classified twice: {sorted(write & read)}"
    unclassified = public - write - read
    assert not unclassified, f"classify these as write or read-only safe: {sorted(unclassified)}"
    assert not ((write | read) - public), f"stale names: {sorted((write | read) - public)}"


@pytest.mark.parametrize("name", sorted(core.STORE_WRITE_METHODS))
def test_write_verbs_on_a_read_only_handle_return_an_error_and_write_nothing(seeded, name):
    before = _snapshot(seeded)
    handle = Engram(seeded, read_only=True)
    method = getattr(handle, name)

    result = method(**_dummy_args(method))

    assert isinstance(result, dict) and result.get("error") == "read_only", name
    assert _snapshot(seeded) == before, name


def test_the_write_entry_points_are_a_backstop(seeded):
    handle = Engram(seeded, read_only=True)

    with pytest.raises(ReadOnlyStoreError):
        handle._update_entries(seeded / "knowledge" / "lessons.json", "lesson", lambda rows: rows)
    with pytest.raises(ReadOnlyStoreError):
        handle._write_playbook_and_index({"id": "abc", "title": "x"})
    with pytest.raises(ReadOnlyStoreError):
        handle._update_playbook_file_by_id("abc", lambda row: row)


@pytest.fixture
def guard_spy(monkeypatch):
    hits: list[str] = []
    for name in core.STORE_WRITE_METHODS:
        guarded = getattr(Engram, name)

        def spy(self, *args, _guarded=guarded, _name=name, **kwargs):
            if getattr(self, "_read_only", False):
                hits.append(_name)
            return _guarded(self, *args, **kwargs)

        monkeypatch.setattr(Engram, name, spy)
    return hits


@pytest.mark.parametrize("name", sorted(core.READ_ONLY_SAFE_METHODS))
def test_read_methods_never_reach_a_write_on_a_read_only_handle(seeded, guard_spy, name):
    handle = Engram(seeded, read_only=True)
    before = _snapshot(seeded)
    method = getattr(handle, name)

    try:
        method(**_dummy_args(method))
    except ReadOnlyStoreError:
        pytest.fail(f"{name} hit the write backstop on a read-only handle")
    except Exception:
        pass  # a dummy argument may be rejected; only writes matter here

    assert guard_spy == [], f"{name} called write verbs {guard_spy} on a read-only handle"
    assert _snapshot(seeded) == before, f"{name} changed the store on a read-only handle"


def _read_tools() -> list[str]:
    import piia_engram.mcp_server as m

    return sorted(n for n, c in m.TOOL_GOVERNANCE_CLASS.items() if c == "read")


@pytest.mark.parametrize("tool_name", _read_tools())
def test_mcp_read_tools_on_a_read_only_handle_never_write(seeded, guard_spy, monkeypatch, tool_name):
    import piia_engram.mcp_server as m

    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    m._engram = Engram(seeded, read_only=True)
    before = _snapshot(seeded)
    func = getattr(m, tool_name)
    kwargs = {}
    for pname, param in inspect.signature(func).parameters.items():
        if param.default is inspect.Parameter.empty:
            kwargs[pname] = 1 if param.annotation is int else False if param.annotation is bool else "x"

    try:
        asyncio.run(func(**kwargs))
    except ReadOnlyStoreError:
        pytest.fail(f"{tool_name} hit the write backstop on a read-only handle")
    except Exception:
        pass

    assert guard_spy == [], f"{tool_name} called write verbs {guard_spy} on a read-only handle"
    assert _snapshot(seeded) == before, f"{tool_name} changed the store through a read-only handle"


def test_default_handles_still_write(seeded):
    eng = Engram(seeded)

    row = eng.add_lesson("a normal write still works", domain="t")

    assert row.get("id") and row.get("status", "active") == "active"
    assert json.loads((seeded / "knowledge" / "lessons.json").read_text(encoding="utf-8"))


def test_mcp_checkpoint_cadence_on_a_read_only_handle_writes_nothing(seeded, guard_spy, monkeypatch):
    """The MCP session checkpoint (every N calls) must not write through a read-only store."""
    import piia_engram.mcp_server as m

    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    m._engram = Engram(seeded, read_only=True)
    m._session._real_call_count = m._session._CHECKPOINT_EVERY - 1
    before = _snapshot(seeded)

    asyncio.run(m.get_playbooks())

    assert guard_spy == []
    assert _snapshot(seeded) == before
