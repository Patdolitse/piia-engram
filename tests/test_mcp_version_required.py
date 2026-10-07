"""MCP writes that change an existing entry must carry ``expected_version``.

* missing   -> ``version_required`` with the current version and an example; nothing written
* stale     -> ``version_conflict`` with the current version; nothing written
* current   -> the write goes through
* new entries and reads need no version; reads return ``version``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    old_session = mcp_server._session
    try:
        old_session._stop_event.set()
    except Exception:
        pass
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_track_count", 0, raising=False)
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _json(text: str) -> dict:
    return json.loads(text)


def _store(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for sub in ("knowledge", "playbooks") if (root / sub).exists()
        for p in sorted((root / sub).rglob("*")) if p.is_file()
    }


def _row(eng: Engram, item_id: str) -> dict:
    return eng._find_item_by_id(item_id)[1]


def _lesson(eng: Engram, summary: str) -> dict:
    return eng.add_lesson({"summary": summary, "domain": "workflow", "tier": "verified"})


def _decision(eng: Engram, question: str, choice: str) -> dict:
    return eng.add_decision({"question": question, "choice": choice, "tier": "verified"})


def _playbook(eng: Engram, title: str) -> dict:
    return eng.add_playbook({"title": title, "steps": [{"action": f"{title} one"}, {"action": f"{title} two"}]})


def _assert_required(result: dict, version: int) -> None:
    assert result["error"] == "version_required", result
    assert result["current_version"] == version
    assert isinstance(result["example"], dict) and result["example"]


def _assert_conflict(result: dict, version: int) -> None:
    assert result["error"] == "version_conflict", result
    assert result["current_version"] == version


# ---------------------------------------------------------------------------
# update_knowledge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_update_knowledge_requires_the_version(eng, kind):
    if kind == "lesson":
        item, updates = _lesson(eng, "Run the linter before every push"), {"detail": "added detail"}
    elif kind == "decision":
        item, updates = _decision(eng, "Which linter do we use?", "ruff"), {"reasoning": "fast"}
    else:
        item, updates = _playbook(eng, "Lint the repository"), {"description": "added description"}
    before = _store(eng.root)

    missing = _json(_run(mcp_server.update_knowledge(item["id"], json.dumps(updates))))
    _assert_required(missing, 1)
    assert missing["example"]["expected_version"] == 1
    assert missing["example"]["item_id"] == item["id"]
    assert _store(eng.root) == before

    stale = _json(_run(mcp_server.update_knowledge(item["id"], json.dumps(updates), expected_version=7)))
    _assert_conflict(stale, 1)
    assert _store(eng.root) == before

    ok = _json(_run(mcp_server.update_knowledge(item["id"], json.dumps(updates), expected_version=1)))
    assert "error" not in ok
    assert int(_row(eng, item["id"]).get("version") or 1) == 2

    again = _json(_run(mcp_server.update_knowledge(item["id"], json.dumps({**updates, "x": 1}),
                                                   expected_version=1)))
    _assert_conflict(again, 2)


def test_update_knowledge_unknown_id_needs_no_version(eng):
    result = _json(_run(mcp_server.update_knowledge("no-such-id", json.dumps({"detail": "x"}))))
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# archive_knowledge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_archive_knowledge_requires_the_version(eng, kind):
    item = {"lesson": lambda: _lesson(eng, "Archive me: an old note about the build agent"),
            "decision": lambda: _decision(eng, "Which build agent?", "agent one"),
            "playbook": lambda: _playbook(eng, "Restart the build agent")}[kind]()
    before = _store(eng.root)
    _assert_required(_json(_run(mcp_server.archive_knowledge(item["id"]))), 1)
    _assert_conflict(_json(_run(mcp_server.archive_knowledge(item["id"], expected_version=3))), 1)
    assert _store(eng.root) == before
    result = _json(_run(mcp_server.archive_knowledge(item["id"], expected_version=1)))
    assert "error" not in result, result
    assert _row(eng, item["id"])["status"] == "outdated"


# ---------------------------------------------------------------------------
# merge_knowledge
# ---------------------------------------------------------------------------


def test_merge_knowledge_requires_both_versions(eng):
    primary = _lesson(eng, "Keep the release checklist in the repository")
    secondary = _lesson(eng, "Store the release checklist next to the code")
    before = _store(eng.root)

    for kwargs, param in (({}, "primary_expected_version"),
                          ({"primary_expected_version": 1}, "secondary_expected_version"),
                          ({"secondary_expected_version": 1}, "primary_expected_version")):
        result = _json(_run(mcp_server.merge_knowledge(primary["id"], secondary["id"], **kwargs)))
        _assert_required(result, 1)
        assert result["param"] == param
    result = _json(_run(mcp_server.merge_knowledge(primary["id"], secondary["id"],
                                                   primary_expected_version=1, secondary_expected_version=2)))
    _assert_conflict(result, 1)
    assert _store(eng.root) == before

    result = _json(_run(mcp_server.merge_knowledge(primary["id"], secondary["id"],
                                                   primary_expected_version=1, secondary_expected_version=1)))
    assert result.get("success") is True or result.get("status") != "error", result
    assert _row(eng, secondary["id"])["status"] == "outdated"


# ---------------------------------------------------------------------------
# manage_playbook
# ---------------------------------------------------------------------------


def test_manage_playbook_update_archive_delete_restore_require_the_version(eng):
    pb = _playbook(eng, "Publish the documentation site")
    before = _store(eng.root)
    for action, extra in (("update", {"title": "Publish the docs"}), ("archive", {}),
                          ("delete", {"dry_run": False, "confirm": True})):
        _assert_required(_json(_run(mcp_server.manage_playbook(action, pb["id"], **extra))), 1)
        _assert_conflict(_json(_run(mcp_server.manage_playbook(action, pb["id"], expected_version=5, **extra))), 1)
    assert _store(eng.root) == before

    # a dry-run delete writes nothing and needs no version
    preview = _json(_run(mcp_server.manage_playbook("delete", pb["id"])))
    assert preview["dry_run"] is True

    ack = _run(mcp_server.manage_playbook("update", pb["id"], title="Publish the docs", expected_version=1))
    assert "error" not in ack
    assert eng._read_playbook_by_id(pb["id"])["version"] == 2
    deleted = _json(_run(mcp_server.manage_playbook("delete", pb["id"], dry_run=False, confirm=True,
                                                    expected_version=2)))
    assert deleted["dry_run"] is False
    version = eng._read_playbook_by_id(pb["id"])["version"]
    _assert_required(_json(_run(mcp_server.manage_playbook("restore", pb["id"], dry_run=False, confirm=True))),
                     version)
    restored = _json(_run(mcp_server.manage_playbook("restore", pb["id"], dry_run=False, confirm=True,
                                                     expected_version=version)))
    assert restored["dry_run"] is False


def test_strict_playbook_update_proposal_requires_the_version(eng, monkeypatch):
    pb = _playbook(eng, "Rotate the API keys")
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    before = _store(eng.root)
    _assert_required(_json(_run(mcp_server.manage_playbook("update", pb["id"], title="Rotate keys"))), 1)
    assert _store(eng.root) == before
    result = _json(_run(mcp_server.manage_playbook("update", pb["id"], title="Rotate keys", expected_version=1)))
    assert result["status"] == "pending" and result["pending_supersedes"] == pb["id"]


# ---------------------------------------------------------------------------
# supersede proposals
# ---------------------------------------------------------------------------


def test_add_decision_supersedes_requires_the_target_version(eng):
    old = _decision(eng, "Which cloud region?", "region one")
    before = _store(eng.root)
    missing = _json(_run(mcp_server.add_decision(question="Which cloud region now?", choice="region two",
                                                 supersedes=old["id"], user_confirmed=True)))
    _assert_required(missing, 1)
    assert missing["param"] == "supersedes_expected_version"
    assert missing["example"]["supersedes"] == old["id"]
    stale = _json(_run(mcp_server.add_decision(question="Which cloud region now?", choice="region two",
                                               supersedes=old["id"], supersedes_expected_version=4,
                                               user_confirmed=True)))
    _assert_conflict(stale, 1)
    assert _store(eng.root) == before

    ok = _run(mcp_server.add_decision(question="Which cloud region now?", choice="region two",
                                      supersedes=old["id"], supersedes_expected_version=1, user_confirmed=True))
    assert ok.startswith("[Engram]")


def test_memory_store_supersedes_requires_the_target_version(eng):
    old = _decision(eng, "Which queue library?", "library one")
    before = _store(eng.root)
    content = {"question": "Which queue library now?", "choice": "library two", "supersedes": old["id"]}
    _assert_required(_json(_run(mcp_server.memory_store(kind="decision", content_json=json.dumps(content),
                                                        user_confirmed=True))), 1)
    batch = [dict(content)]
    _assert_required(_json(_run(mcp_server.memory_store(kind="decision", items_json=json.dumps(batch),
                                                        user_confirmed=True))), 1)
    _assert_conflict(_json(_run(mcp_server.memory_store(
        kind="decision", content_json=json.dumps({**content, "supersedes_expected_version": 9}),
        user_confirmed=True))), 1)
    assert _store(eng.root) == before
    ok = _run(mcp_server.memory_store(kind="decision",
                                      content_json=json.dumps({**content, "supersedes_expected_version": 1}),
                                      user_confirmed=True))
    assert ok.startswith("[Engram]")
    stored = [r for r in json.loads((eng.root / "knowledge" / "decisions.json").read_text(encoding="utf-8"))
              if r.get("question") == "Which queue library now?"]
    assert stored and "supersedes_expected_version" not in stored[0]


def test_supersedes_an_unknown_id_is_refused(eng):
    before = _store(eng.root)
    result = _json(_run(mcp_server.add_decision(question="Which editor?", choice="any", supersedes="nope",
                                                supersedes_expected_version=1, user_confirmed=True)))
    assert result["error"] == "supersedes_target_not_found"
    assert _store(eng.root) == before


# ---------------------------------------------------------------------------
# new entries are unaffected
# ---------------------------------------------------------------------------


def test_new_entries_need_no_version(eng):
    assert _run(mcp_server.add_lesson(summary="A brand new lesson about log rotation",
                                      user_confirmed=True)).startswith("[Engram]")
    assert _run(mcp_server.add_decision(question="Which log format?", choice="JSON lines",
                                        user_confirmed=True)).startswith("[Engram]")
    assert "Playbook" in _run(mcp_server.add_playbook(title="Rotate the logs", triggers="logs",
                                                      steps_json=json.dumps([{"action": "rotate"}]),
                                                      user_confirmed=True))
    assert _run(mcp_server.memory_store(kind="lesson", content_json=json.dumps(
        {"summary": "Another new lesson about disk alerts"}), user_confirmed=True)).startswith("[Engram]")


# ---------------------------------------------------------------------------
# reads carry the version
# ---------------------------------------------------------------------------


def test_read_tools_return_the_version(eng):
    lesson = _lesson(eng, "Version visible lesson about zqversion caching")
    decision = _decision(eng, "Version visible decision about zqversion storage?", "files")
    pb = _playbook(eng, "Version visible zqversion playbook")
    eng.update_knowledge(lesson["id"], {"detail": "revised"})

    lessons = _json(_run(mcp_server.get_lessons()))
    assert all("version" in row for row in lessons)
    assert {row["id"]: row["version"] for row in lessons}[lesson["id"]] == 2
    decisions = _json(_run(mcp_server.get_decisions()))
    assert all("version" in row for row in decisions)

    found = _json(_run(mcp_server.search_knowledge("zqversion")))
    rows = [*found.get("lessons", []), *found.get("decisions", []), *found.get("playbooks", [])]
    assert rows and all("version" in row for row in rows)

    relevant = _json(_run(mcp_server.get_relevant_knowledge(project_folder="")))
    assert all("version" in row for row in relevant["items"])

    listed = _json(_run(mcp_server.get_playbooks()))
    assert all("version" in row for row in listed)
    one = _json(_run(mcp_server.get_playbooks(playbook_id=pb["id"])))
    assert one["version"] == 1
    managed = _json(_run(mcp_server.get_playbooks(mode="management", limit=100)))
    assert all("version" in row for row in managed["items"])

    history = _json(_run(mcp_server.get_knowledge_history(lesson["id"])))
    assert history["current_version"] == 2
    history = _json(_run(mcp_server.get_knowledge_history(decision["id"])))
    assert history["current_version"] == 1
