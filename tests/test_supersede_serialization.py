"""Disk-backed replacement serialization and portable lineage regressions."""
import asyncio
import json
import threading

import pytest

from piia_engram import Engram


def _mcp_pair(tmp_path, monkeypatch):
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": "Choose cache policy", "choice": "ttl", "tier": "verified"})
    local = Engram(root=eng.root)
    monkeypatch.setattr(server, "_engram", eng)
    monkeypatch.setattr(server, "_track", lambda *a, **k: None)
    return server, eng, local, old


def _replace(server, old, *, batch=False):
    content = {"question": "Choose cache policy", "choice": "bounded", "supersedes": old["id"],
               "supersedes_expected_version": 1}
    args = {"items_json": json.dumps([content])} if batch else {"content_json": json.dumps(content)}
    return asyncio.run(server.memory_store(kind="decision", user_confirmed=True, **args))


@pytest.mark.parametrize("writer", ["pin", "version"])
@pytest.mark.parametrize("point", ["validated", "persisted"])
@pytest.mark.parametrize("batch", [False, True])
def test_local_writer_cannot_enter_replacement_transaction(tmp_path, monkeypatch, writer, point, batch):
    import portalocker
    server, eng, local, old = _mcp_pair(tmp_path, monkeypatch)
    from piia_engram import mcp_tools_write, pinning, storage
    outcomes = []
    original_lock = storage._directory_lock

    def nonblocking_local_lock(path, **kwargs):
        if threading.current_thread().name == "local-owner-writer":
            return portalocker.Lock(path, "a", timeout=0,
                                    flags=portalocker.LOCK_EX | portalocker.LOCK_NB)
        return original_lock(path, **kwargs)

    monkeypatch.setattr(storage, "_directory_lock", nonblocking_local_lock)

    def interleave():
        def write():
            try:
                if writer == "pin":
                    outcomes.append(pinning.pin(local, old["id"]))
                else:
                    outcomes.append(local.update_decision(old["id"], {"reasoning": "Owner revision"}, expected_version=1))
            except portalocker.LockException:
                outcomes.append("locked")
            except RuntimeError as exc:
                if not isinstance(exc.__context__, portalocker.LockException):
                    raise
                outcomes.append("locked")
        thread = threading.Thread(target=write, name="local-owner-writer")
        thread.start()
        thread.join(3)
        assert not thread.is_alive()

    if point == "validated":
        original_guard = mcp_tools_write._supersede_refusal
        calls = []
        def guard(*args, **kwargs):
            result = original_guard(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                interleave()
            return result
        monkeypatch.setattr(mcp_tools_write, "_supersede_refusal", guard)
    else:
        original_commit = eng._commit_version_edge
        def commit(*args, **kwargs):
            interleave()
            return original_commit(*args, **kwargs)
        monkeypatch.setattr(eng, "_commit_version_edge", commit)
    response = _replace(server, old, batch=batch)
    assert outcomes == ["locked"], (outcomes, response)
    prior = eng._find_item_by_id(old["id"])[1]
    assert int(prior.get("version") or 1) == 1 and prior["status"] == "superseded"


@pytest.mark.parametrize("writer", ["pin", "version"])
@pytest.mark.parametrize("batch", [False, True])
def test_local_change_before_locked_validation_is_respected(tmp_path, monkeypatch, writer, batch):
    from piia_engram.governance_store import RelationStore
    server, eng, local, old = _mcp_pair(tmp_path, monkeypatch)
    from piia_engram import mcp_tools_write, pinning
    original_guard = mcp_tools_write._supersede_refusal
    calls = []
    before = {}
    def guard(*args, **kwargs):
        result = original_guard(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            if writer == "pin":
                assert pinning.pin(local, old["id"])["status"] == "pinned"
            else:
                assert local.update_decision(old["id"], {"reasoning": "Owner revision"}, expected_version=1)["version"] == 2
            before.update({str(p): p.read_bytes() for p in eng._knowledge_dir.rglob("*") if p.is_file()})
        return result
    monkeypatch.setattr(mcp_tools_write, "_supersede_refusal", guard)
    response = _replace(server, old, batch=batch)
    prior = eng._find_item_by_id(old["id"])[1]
    assert prior["status"] == "active"
    if writer == "version":
        assert json.loads(response)["error"] == "version_conflict"
        assert {str(p): p.read_bytes() for p in eng._knowledge_dir.rglob("*") if p.is_file()} == before
    else:
        assert prior["pinned"] is True
        rows = json.loads((eng._knowledge_dir / "decisions.json").read_text(encoding="utf-8"))
        proposal = next(row for row in rows if row["id"] != old["id"])
        assert proposal["tier"] == "staging" and proposal["pending_supersedes"] == old["id"]
        assert RelationStore(eng.root).all_edges() == []


