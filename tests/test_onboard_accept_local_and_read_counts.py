"""Two review boundaries.

* Accepting an onboard candidate is the Owner's local command
  (``engram onboard-accept``): over MCP, in every approval mode, it answers
  ``local_review_only`` and writes nothing; the local accept is unchanged.
* Reads only count an access: they never refresh ``last_reviewed``, which only
  the Owner's confirm / review actions set. What get_stale_knowledge reports is
  the same before and after a read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server, write_provenance
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _digest(root: Path) -> dict[str, str]:
    out = {}
    for sub in ("knowledge", "playbooks", "identity"):
        base = root / sub
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.name != ".engram-write.lock":
                    out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _candidate(eng: Engram, tmp_path: Path) -> tuple[dict, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "README.md").write_text("hello", encoding="utf-8")
    row = eng.add_lesson({"summary": "The repo keeps its docs in README.md", "tier": "staging",
                          "provenance": {"anchor_ref": "file:README.md", "confirmation_source": "anchor"}},
                         _allow_internal_provenance=True)
    return row, repo


@pytest.mark.parametrize("mode", ["default", "strict"])
def test_mcp_onboard_accept_is_local_only(eng, tmp_path, monkeypatch, mode):
    row, repo = _candidate(eng, tmp_path)
    if mode == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    before = _digest(eng.root)
    text = _run(mcp_server.onboard_accept(row["id"], project_root=str(repo)))
    assert _digest(eng.root) == before
    if mode == "default":
        reply = json.loads(text)
        assert reply["error"] == "local_review_only" and "engram onboard-accept" in reply["hint"]
    assert eng._find_item_by_id(row["id"])[1]["tier"] == "staging"


def test_core_onboard_accept_refuses_under_mcp_origin(eng, tmp_path):
    row, repo = _candidate(eng, tmp_path)
    before = _digest(eng.root)
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        single = eng.accept_onboard_candidate(row["id"], project_root=str(repo))
        batch = eng.accept_onboard_candidates(project_root=str(repo))
    assert single["error"] == "local_review_only"
    assert batch["error"] == "local_review_only"
    assert _digest(eng.root) == before


def test_local_onboard_accept_is_unchanged(eng, tmp_path):
    row, repo = _candidate(eng, tmp_path)
    accepted = eng.accept_onboard_candidate(row["id"], project_root=str(repo))
    assert "error" not in accepted
    assert eng._find_item_by_id(row["id"])[1]["tier"] == "verified"


def test_onboard_accept_tool_is_annotated_read_only():
    from piia_engram.tool_annotations import TOOL_ANNOTATIONS

    hints = TOOL_ANNOTATIONS["onboard_accept"]
    assert hints.read_only and not hints.destructive


def _age(eng: Engram, days_old: str = "2020-01-01T00:00:00Z") -> None:
    for name in ("lessons.json", "decisions.json"):
        path = eng.root / "knowledge" / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        for r in rows:
            r["last_reviewed"] = days_old
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_reads_count_access_but_do_not_refresh_last_reviewed(eng):
    lesson = eng.add_lesson("Rotate the deploy keys every quarter", domain="ops")
    decision = eng.add_decision("Which registry hosts the images?", choice="the internal one")
    pb = eng.add_playbook({"title": "Rotate the deploy keys", "steps": [{"action": "rotate"}]})
    _age(eng)
    def _stale_ids():
        stale = eng.get_stale_knowledge(days=30, limit=50)
        return {k: sorted(r["id"] for r in stale.get(k, [])) for k in ("lessons", "decisions")}

    stale_before = _stale_ids()
    assert lesson["id"] in stale_before["lessons"] and decision["id"] in stale_before["decisions"]

    _run(mcp_server.get_lessons())
    _run(mcp_server.get_decisions())
    eng.get_lessons(limit=None)
    eng.get_decisions(limit=None)
    eng.get_playbooks()
    eng.get_playbook(pb["id"])

    assert _stale_ids() == stale_before
    rows = {r["id"]: r for r in eng.get_lessons(limit=None, _update_access=False)}
    assert rows[lesson["id"]]["last_reviewed"] == "2020-01-01T00:00:00Z"
    assert rows[lesson["id"]]["access_count"] >= 1
    drows = {r["id"]: r for r in eng.get_decisions(limit=None, _update_access=False)}
    assert drows[decision["id"]]["last_reviewed"] == "2020-01-01T00:00:00Z"
    assert drows[decision["id"]]["access_count"] >= 1
    before_pb = pb.get("last_reviewed")
    assert eng._read_playbook_by_id(pb["id"])["last_reviewed"] == before_pb


def test_owner_review_still_refreshes_last_reviewed(eng):
    lesson = eng.add_lesson("Keep the changelog bilingual", domain="docs")
    _age(eng)
    eng.review_knowledge(lesson["id"])
    rows = {r["id"]: r for r in eng.get_lessons(limit=None, _update_access=False)}
    assert rows[lesson["id"]]["last_reviewed"] != "2020-01-01T00:00:00Z"
