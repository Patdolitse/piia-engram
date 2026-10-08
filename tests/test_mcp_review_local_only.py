"""Deciding a pending proposal is the Owner's local review, in every mode.

Over MCP, approving, promoting, rejecting or archiving a pending (staging)
row is refused with ``local_review_only`` and nothing is written -- in default
mode as well as under strict. Listing and dry-run previews stay available.
The rule lives in the shared core operations, so every MCP entry point
(review_staging batch / apply_text, update_knowledge, archive_knowledge,
manage_playbook, confirm_knowledge, onboard_accept) gets it. Local Owner
paths (engram review) are unchanged.

An AI's content change of an approved playbook is a pending revision proposal
through every MCP entry point, update_knowledge included.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, review_cli, write_provenance
from piia_engram.core import Engram


@pytest.fixture(params=["default", "strict"])
def eng(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    engram._test_mode = request.param
    return engram


def _strict(eng, monkeypatch):
    if eng._test_mode == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")


def _run(coro):
    return asyncio.run(coro)


def _knowledge_digest(root: Path) -> dict[str, str]:
    out = {}
    for sub in ("knowledge", "playbooks", "identity"):
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    tomb = root / "tombstones.jsonl"
    if tomb.exists():
        out["tombstones"] = hashlib.sha256(tomb.read_bytes()).hexdigest()
    return out


def _refused(text: str) -> bool:
    return "local_review_only" in text or "strict" in text.lower() or "refus" in text.lower()


def _pending_playbook(eng) -> str:
    reply = json.loads(_run(mcp_server.add_playbook(
        title="Publish the release notes", triggers="release",
        steps_json=json.dumps([{"action": "write"}]), user_confirmed=True)))
    assert reply["status"] == "pending"
    return reply["id"]


_TOPICS = iter(["cache headers", "retry budgets", "font loading", "queue depth", "lock files",
                "log rotation", "index sizes", "token expiry"] * 4)


def _lesson(eng, tier="staging") -> dict:
    return eng.add_lesson(f"A {tier} lesson about {next(_TOPICS)} in production", tier=tier)


def test_batch_approve_and_reject_are_local_only(eng, monkeypatch):
    pb = _pending_playbook(eng)
    lesson = _lesson(eng)
    _strict(eng, monkeypatch)
    before = _knowledge_digest(eng.root)
    for act in ("approve", "reject", "archive"):
        for item in (pb, lesson["id"]):
            text = _run(mcp_server.review_staging(
                action="batch", actions_json=json.dumps([{"id": item, "action": act}]),
                dry_run=False, confirm=True))
            assert _refused(text), text
    assert _knowledge_digest(eng.root) == before
    assert eng._read_playbook_by_id(pb)["tier"] == "staging"


def test_default_mode_reply_names_the_local_review(eng):
    if eng._test_mode != "default":
        pytest.skip("default-mode wording")
    pb = _pending_playbook(eng)
    reply = json.loads(_run(mcp_server.review_staging(
        action="batch", actions_json=json.dumps([{"id": pb, "action": "approve"}]),
        dry_run=False, confirm=True)))
    assert reply["error"] == "local_review_only" and "engram review" in reply["hint"]


def test_list_and_dry_run_preview_stay_available(eng, monkeypatch):
    pb = _pending_playbook(eng)
    _strict(eng, monkeypatch)
    listed = json.loads(_run(mcp_server.review_staging(action="list")))
    assert "error" not in listed
    preview = json.loads(_run(mcp_server.review_staging(
        action="batch", actions_json=json.dumps([{"id": pb, "action": "approve"}]), dry_run=True)))
    assert preview.get("status") == "dry_run" and preview.get("changed") is False


def test_apply_text_is_local_only(eng, monkeypatch):
    lesson = _lesson(eng)
    verified = _lesson(eng, tier="verified")
    _strict(eng, monkeypatch)
    before = _knowledge_digest(eng.root)
    for text in (f"promote lesson {lesson['id']}", f"archive lesson {lesson['id']}",
                 json.dumps({"promote": [{"type": "lesson", "id": lesson["id"]}], "archive": []}),
                 json.dumps({"archive": [{"type": "lesson", "id": verified["id"]}]})):
        reply = _run(mcp_server.review_staging(action="apply_text", review_text=text))
        assert _refused(reply), reply
    assert _knowledge_digest(eng.root) == before


def test_update_knowledge_cannot_promote_or_archive_a_pending_row(eng, monkeypatch):
    lesson = _lesson(eng)
    pb = _pending_playbook(eng)
    _strict(eng, monkeypatch)
    before = _knowledge_digest(eng.root)
    for item, updates in ((lesson["id"], {"tier": "verified"}), (lesson["id"], {"status": "outdated"}), (lesson["id"], {"status": "rejected"}),
                          (lesson["id"], {"tier": "archived"}), (pb, {"status": "outdated"})):
        reply = _run(mcp_server.update_knowledge(item, json.dumps(updates), expected_version=1))
        assert _refused(reply), reply
    assert _knowledge_digest(eng.root) == before


def test_archive_and_manage_playbook_cannot_retire_a_pending_row(eng, monkeypatch):
    lesson = _lesson(eng)
    pb = _pending_playbook(eng)
    _strict(eng, monkeypatch)
    before = _knowledge_digest(eng.root)
    assert _refused(_run(mcp_server.archive_knowledge(lesson["id"], expected_version=1)))
    assert _refused(_run(mcp_server.archive_knowledge(pb, expected_version=1)))
    assert _refused(_run(mcp_server.manage_playbook("archive", pb, expected_version=1)))
    assert _refused(_run(mcp_server.manage_playbook("delete", pb, dry_run=False, confirm=True,
                                                    expected_version=1)))
    assert _knowledge_digest(eng.root) == before


def test_confirm_knowledge_cannot_stamp_a_pending_row(eng, monkeypatch):
    lesson = _lesson(eng)
    _strict(eng, monkeypatch)
    before = _knowledge_digest(eng.root)
    assert _refused(_run(mcp_server.confirm_knowledge(lesson["id"], by="human")))
    assert _knowledge_digest(eng.root) == before


@pytest.mark.parametrize("call", ["promote", "approve_playbook", "reject_playbook", "archive", "apply_review",
                                  "batch"])
def test_core_operations_refuse_under_mcp_origin(eng, call):
    from piia_engram.staging_review import batch_review_staging

    lesson = _lesson(eng)
    with write_provenance.origin_scope(write_provenance.ORIGIN_LOCAL):
        pass
    pb = _pending_playbook(eng)
    before = _knowledge_digest(eng.root)
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        if call == "promote":
            result = eng.promote_knowledge(lesson["id"])
        elif call == "approve_playbook":
            result = eng.approve_playbook(pb)
        elif call == "reject_playbook":
            result = eng.reject_playbook(pb, _owner_reject="mcp:test")
        elif call == "archive":
            result = eng.archive_knowledge(lesson["id"])
        elif call == "apply_review":
            result = eng.apply_review(f"promote lesson {lesson['id']}")
        else:
            result = batch_review_staging(eng, [{"id": pb, "action": "approve"}], dry_run=False, confirm=True)
    assert "local_review_only" in json.dumps(result)
    assert _knowledge_digest(eng.root) == before


def test_local_owner_review_still_approves(eng, tmp_path):
    pb = _pending_playbook(eng)
    lesson = _lesson(eng)
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": pb, "mark": "approve"}, {"id": lesson["id"], "mark": "approve"}]),
                     encoding="utf-8")
    assert review_cli.run_apply([str(marks), "--operator", "owner", "--yes"]) == 0
    assert eng._read_playbook_by_id(pb)["tier"] == "verified"
    assert eng.promote_knowledge(_lesson(eng)["id"])["status"] == "promoted"  # local origin


def test_mcp_can_still_archive_a_verified_unpinned_lesson(eng):
    if eng._test_mode != "default":
        pytest.skip("strict refuses archive_knowledge entirely")
    verified = _lesson(eng, tier="verified")
    reply = json.loads(_run(mcp_server.archive_knowledge(verified["id"], expected_version=1)))
    assert reply.get("status") in ("archived", "outdated") or not reply.get("error"), reply


# ---------------------------------------------------------------------------
# finding: update_knowledge edits approved playbooks in place
# ---------------------------------------------------------------------------


def test_update_knowledge_on_an_approved_playbook_is_a_proposal(eng, monkeypatch):
    approved = eng.add_playbook({"title": "Ship the desktop build", "steps": [{"action": "old one"}]})
    _strict(eng, monkeypatch)
    before = (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes()
    text = _run(mcp_server.update_knowledge(approved["id"], json.dumps({"steps": [{"action": "new"}]}),
                                            expected_version=1))
    assert (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes() == before
    if eng._test_mode == "strict":
        assert _refused(text)
        return
    reply = json.loads(text)
    assert reply["status"] == "pending" and reply["pending_supersedes"] == approved["id"]
    proposal = eng._read_playbook_by_id(reply["id"])
    assert proposal["tier"] == "staging" and [s["action"] for s in proposal["steps"]] == ["new"]


def test_core_update_playbook_under_mcp_origin_is_a_proposal(eng):
    approved = eng.add_playbook({"title": "Rotate the API tokens", "steps": [{"action": "old"}]})
    before = (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes()
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        reply = eng.update_playbook(approved["id"], {"steps": [{"action": "new"}]}, expected_version=1)
        mixed = eng.update_playbook(approved["id"], {"steps": [{"action": "x"}], "status": "outdated"},
                                    expected_version=1)
        stale = eng.update_playbook(approved["id"], {"steps": [{"action": "y"}]}, expected_version=7)
    assert (eng.root / "playbooks" / f"{approved['id']}.json").read_bytes() == before
    assert reply["status"] == "pending" and reply["pending_supersedes"] == approved["id"]
    assert mixed.get("error") == "mixed_update"
    assert stale.get("error") == "version_conflict"


def test_local_update_playbook_still_edits_in_place(eng):
    approved = eng.add_playbook({"title": "Local edit playbook", "steps": [{"action": "old"}]})
    result = eng.update_playbook(approved["id"], {"steps": [{"action": "new"}]}, expected_version=1)
    assert result["version"] == 2 and result["tier"] == "verified"
