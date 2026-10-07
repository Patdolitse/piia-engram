"""Playbook ids name files, so callers never choose them over MCP.

* memory_store(kind="playbook") / add_playbook over MCP always get a
  server-generated id; a caller-supplied id is ignored;
* an id that is not a plain file-name token (traversal, separators, drive
  letters) is refused by every playbook file operation;
* a local insert never overwrites an existing playbook id;
* execution-plan files and daily-log dates are held to the same rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, pinning
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _digest(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "audit" not in path.parts and "logs" not in path.parts:
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _identity_files(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (root / "identity").glob("*.json")}


@pytest.mark.parametrize("mode", ["default", "strict"])
@pytest.mark.parametrize("bad_id", ["../identity/profile", "..\\identity\\profile", "../../outside",
                                    "C:/outside", "/abs/outside", "a/b"])
def test_mcp_traversal_id_never_writes_outside_playbooks(eng, monkeypatch, mode, bad_id):
    if mode == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    eng.update_profile({"role": "original role"})
    before = _identity_files(eng.root)
    text = _run(mcp_server.memory_store(kind="playbook", content_json=json.dumps(
        {"id": bad_id, "title": "Traversal attempt playbook", "role": "attacker role",
         "steps": [{"action": "x"}]}), user_confirmed=True))
    assert _identity_files(eng.root) == before
    assert not (eng.root.parent / "outside.json").exists()
    # the insert either lands under a fresh server id or is refused; never under bad_id
    for path in (eng.root / "playbooks").glob("*.json"):
        if path.name.startswith("_"):
            continue
        assert path.stem != bad_id
        assert path.resolve().parent == (eng.root / "playbooks").resolve()
    assert "attacker role" not in json.dumps(eng.get_profile())
    assert bad_id not in text or "error" in text or "id" in text


@pytest.mark.parametrize("mode", ["default", "strict"])
@pytest.mark.parametrize("pinned", [True, False])
def test_mcp_insert_with_existing_id_leaves_the_existing_playbook(eng, monkeypatch, mode, pinned):
    approved = eng.add_playbook({"title": "Deploy the billing service", "steps": [{"action": "old"}]})
    if pinned:
        assert pinning.pin(eng, approved["id"]).get("status") in ("pinned", "already_pinned")
    body = eng.root / "playbooks" / f"{approved['id']}.json"
    before_body = body.read_bytes()
    before_index = json.loads((eng.root / "playbooks" / "_index.json").read_text(encoding="utf-8"))
    if mode == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    _run(mcp_server.memory_store(kind="playbook", content_json=json.dumps(
        {"id": approved["id"], "title": "Totally different procedure for cache warmup",
         "steps": [{"action": "evil"}]}), user_confirmed=True))
    assert body.read_bytes() == before_body
    index = json.loads((eng.root / "playbooks" / "_index.json").read_text(encoding="utf-8"))
    assert [e for e in index if e.get("id") == approved["id"]] == [
        e for e in before_index if e.get("id") == approved["id"]]
    proposals = [e for e in index if e.get("id") != approved["id"]]
    assert len(proposals) == 1  # the proposal got its own fresh id


def test_mcp_add_playbook_tool_ignores_caller_id_too(eng):
    approved = eng.add_playbook({"title": "Rotate the TLS certificates", "steps": [{"action": "old"}]})
    before = (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes()
    _run(mcp_server.memory_store(kind="playbook", items_json=json.dumps([
        {"id": approved["id"], "title": "Unrelated batch procedure for log shipping",
         "steps": [{"action": "evil"}]}]), user_confirmed=True))
    assert (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes() == before


def test_local_insert_refuses_an_existing_id(eng):
    first = eng.add_playbook({"title": "Restart the queue workers", "steps": [{"action": "a"}]})
    before = (eng.root / "playbooks" / f"{first['id']}.json").read_bytes()
    second = eng.add_playbook({"id": first["id"], "title": "A different procedure about printers",
                               "steps": [{"action": "b"}]})
    assert second.get("status") == "id_exists" or second.get("error")
    assert (eng.root / "playbooks" / f"{first['id']}.json").read_bytes() == before


@pytest.mark.parametrize("bad_id", ["../identity/profile", "..", ".", "", "a/b", "a\\b", "C:x", "x\x00y"])
def test_every_playbook_file_operation_refuses_bad_ids(eng, bad_id):
    eng.update_profile({"role": "original role"})
    before = _digest(eng.root)
    assert eng._read_playbook_by_id(bad_id) is None
    assert eng._update_playbook_file_by_id(bad_id, lambda pb: pb) is None
    with pytest.raises(ValueError):
        eng._write_playbook_and_index({"id": bad_id, "title": "x", "steps": []})
    eng._delete_playbook_snapshot(bad_id)
    assert "error" in eng.update_playbook(bad_id, {"steps": [{"action": "z"}]})
    assert "error" in eng.archive_playbook(bad_id)
    assert "error" in eng.delete_playbook(bad_id)
    assert "error" in eng.restore_playbook(bad_id)
    assert "error" in eng.get_execution_status(bad_id) or eng.get_execution_status(bad_id).get("status")
    assert "error" in eng.update_execution_step(bad_id, 1, "completed")
    assert "error" in eng.save_execution_plan({"playbook_id": bad_id, "execution_plan": []})
    assert _digest(eng.root) == before


def test_execution_step_cannot_rewrite_identity_files(eng):
    eng.update_profile({"role": "original role"})
    before = _identity_files(eng.root)
    reply = json.loads(_run(mcp_server.playbook_execution(
        action="update_step", playbook_id="../../identity/profile", step_order=1, step_status="completed")))
    assert "error" in reply
    assert _identity_files(eng.root) == before


@pytest.mark.parametrize("bad_date", ["../../../outside", "C:/outside", "2026-13-99/../x"])
def test_daily_log_date_must_be_a_plain_date(eng, tmp_path, bad_date):
    (tmp_path / "outside.md").write_text("secret notes", encoding="utf-8")
    reply = eng.get_daily_log("", date=bad_date)
    assert reply.get("content", "") == "" and reply.get("error")


def test_generated_ids_are_still_accepted(eng):
    pb = eng.add_playbook({"title": "Generated id playbook", "steps": [{"action": "a"}]})
    assert eng._read_playbook_by_id(pb["id"])["title"] == "Generated id playbook"
    updated = eng.update_playbook(pb["id"], {"steps": [{"action": "b"}]}, expected_version=1)
    assert updated.get("version") == 2
    # the pre-revision snapshot id ({id}-prev-{stamp}) is a valid id as well
    snaps = [e for e in eng._read_playbook_index() if str(e.get("id", "")).startswith(pb["id"] + "-prev-")]
    assert snaps and eng._read_playbook_by_id(snaps[0]["id"]) is not None
