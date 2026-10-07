"""Owner pins: ``engram pin`` / ``engram unpin`` and what a pin protects.

* only the Owner's local command sets or clears a pin; only trusted entries can
  be pinned; ``pinned`` / ``pinned_at`` in an MCP payload are dropped;
* over MCP a pinned entry cannot be edited, archived, merged or deleted
  (``pinned_entry``, nothing written); a revision proposal that supersedes it
  always waits for the Owner (default and strict mode);
* when the Owner approves that revision the old entry is superseded and its pin
  is removed (audited);
* the lifecycle archive, the capacity rules and imports leave pinned entries alone;
* recall puts pinned entries first in their group; search only uses a pin to
  break a tie and never shows a pinned entry for an unrelated query.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import capacity, mcp_server, pinning, recall_policy, review_cli
from piia_engram.cli_commands import run_pin, run_unpin
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.staging_review import batch_review_staging


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
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
    """Hash of every knowledge and playbook file."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for sub in ("knowledge", "playbooks") if (root / sub).exists()
        for p in sorted((root / sub).rglob("*")) if p.is_file()
    }


def _row(eng: Engram, item_id: str) -> dict:
    return eng._find_item_by_id(item_id)[1]


def _audit(root: Path) -> list[dict]:
    path = root / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _lesson(eng: Engram, summary: str, **extra) -> dict:
    return eng.add_lesson({"summary": summary, "domain": "workflow", "tier": "verified", **extra})


def _decision(eng: Engram, question: str, choice: str) -> dict:
    return eng.add_decision({"question": question, "choice": choice, "tier": "verified"})


def _playbook(eng: Engram, title: str) -> dict:
    return eng.add_playbook({"title": title, "steps": [{"action": f"{title} step one"},
                                                      {"action": f"{title} step two"}]})


def _pin(eng: Engram, item_id: str, capsys=None) -> None:
    assert run_pin([item_id]) == 0
    if capsys is not None:
        capsys.readouterr()


def _version(eng: Engram, item_id: str) -> int:
    return int(_row(eng, item_id).get("version") or 1)


def _strict(monkeypatch) -> None:
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")


# ---------------------------------------------------------------------------
# local CLI
# ---------------------------------------------------------------------------


def test_pin_and_unpin_a_lesson_from_the_cli(eng, capsys):
    lesson = _lesson(eng, "Pin me: run the migration dry run before the real one")
    version = _version(eng, lesson["id"])

    assert run_pin([lesson["id"]]) == 0
    row = _row(eng, lesson["id"])
    assert row["pinned"] is True and row["pinned_at"]
    assert _version(eng, lesson["id"]) == version  # a pin is metadata: the version stays
    assert pinning.is_pinned(row)

    assert run_pin([lesson["id"]]) == 0  # pinning again changes nothing
    assert "already" in capsys.readouterr().out.lower()

    assert run_unpin([lesson["id"]]) == 0
    row = _row(eng, lesson["id"])
    assert "pinned" not in row and "pinned_at" not in row

    events = [e for e in _audit(eng.root) if str(e.get("resource", "")).startswith("pin/")]
    assert [e["resource"] for e in events] == ["pin/pin", "pin/unpin"]
    for event in events:  # metadata only: never the entry's text
        assert "migration" not in json.dumps(event)
        assert event["id"] == lesson["id"] and event["kind"] == "lesson"


def test_pin_with_kind_and_list(eng, capsys):
    decision = _decision(eng, "Which queue backs the jobs?", "the managed queue")
    playbook = _playbook(eng, "Rotate the deploy token")
    assert run_pin([decision["id"], "--kind", "decision"]) == 0
    assert run_pin([playbook["id"], "--kind", "playbook"]) == 0
    assert run_pin([decision["id"], "--kind", "lesson"]) == 1  # wrong kind: not found
    capsys.readouterr()

    assert run_pin(["--list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {(item["kind"], item["id"]) for item in listed["pinned"]} == {
        ("decision", decision["id"]), ("playbook", playbook["id"])}
    assert all(item["state"] == "trusted" for item in listed["pinned"])
    assert eng._read_playbook_by_id(playbook["id"])["pinned"] is True


def test_only_trusted_entries_can_be_pinned(eng, capsys, monkeypatch):
    archived = _lesson(eng, "An archived note about the old build server")
    eng.archive_knowledge(archived["id"])
    old = _decision(eng, "Where do build caches live?", "on each runner")
    new = _decision(eng, "Where do build caches live now?", "in the shared bucket")
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    _strict(monkeypatch)
    pending = eng.add_lesson({"summary": "A pending proposal about cache keys", "domain": "workflow"})
    assert _row(eng, pending["id"])["tier"] == "staging"
    before = _store(eng.root)

    for item_id, state in ((archived["id"], "archived"), (old["id"], "superseded"), (pending["id"], "pending")):
        capsys.readouterr()
        assert run_pin([item_id, "--json"]) == 1
        out = json.loads(capsys.readouterr().out)
        assert out["error"] == "not_trusted" and out["state"] == state
    assert run_pin(["no-such-id"]) == 1
    assert _store(eng.root) == before


def test_pin_usage_errors(eng, capsys):
    assert run_pin([]) == 2
    assert run_unpin([]) == 2
    assert run_pin(["x", "--kind", "tool"]) == 2


def test_cli_dispatch_routes_pin(eng, monkeypatch):
    from piia_engram import setup_wizard as sw

    seen = {}
    monkeypatch.setattr(sw, "run_pin", lambda argv: seen.setdefault("pin", argv) and 0)
    monkeypatch.setattr(sw, "run_unpin", lambda argv: seen.setdefault("unpin", argv) and 0)
    for argv in (["engram", "pin", "abc"], ["engram", "unpin", "abc"]):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit):
            sw.main()
    assert seen == {"pin": ["abc"], "unpin": ["abc"]}


# ---------------------------------------------------------------------------
# MCP: pinned is a system field; pinned entries are read-only over MCP
# ---------------------------------------------------------------------------


def test_mcp_payload_cannot_set_pinned(eng):
    _run(mcp_server.memory_store(kind="lesson", content_json=json.dumps({
        "summary": "A lesson that tries to pin itself on the way in", "domain": "workflow",
        "pinned": True, "pinned_at": "2000-01-01T00:00:00Z"}), user_confirmed=True))
    _run(mcp_server.memory_store(kind="decision", items_json=json.dumps([{
        "question": "Should batch rows pin themselves?", "choice": "no", "pinned": True}]),
        user_confirmed=True))
    _run(mcp_server.add_playbook(title="Self pinning playbook attempt", triggers="pin",
                                 steps_json=json.dumps([{"action": "try"}]), user_confirmed=True))
    rows = [*json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8")),
            *json.loads((eng.root / "knowledge" / "decisions.json").read_text(encoding="utf-8"))]
    assert rows and not any("pinned" in r or "pinned_at" in r for r in rows)
    assert "pinned" in capacity.SYSTEM_FIELDS and "pinned_at" in capacity.SYSTEM_FIELDS

    plain = _lesson(eng, "An unpinned lesson an agent tries to pin by update")
    result = _json(_run(mcp_server.update_knowledge(
        plain["id"], json.dumps({"pinned": True, "pinned_at": "now"}),
        expected_version=_version(eng, plain["id"]))))
    assert "error" not in result or result.get("error") != "pinned_entry"
    assert "pinned" not in _row(eng, plain["id"])


def _pinned_trio(eng):
    lesson = _lesson(eng, "Pinned: tag releases from the release branch only")
    decision = _decision(eng, "Which branch do releases come from?", "the release branch")
    playbook = _playbook(eng, "Cut a release from the release branch")
    for item in (lesson, decision, playbook):
        assert run_pin([item["id"]]) == 0
    return lesson, decision, playbook


def test_mcp_cannot_edit_archive_merge_or_delete_a_pinned_entry(eng, capsys):
    lesson, decision, playbook = _pinned_trio(eng)
    other = _lesson(eng, "An ordinary lesson about release notes wording")
    capsys.readouterr()
    before = _store(eng.root)

    calls = [
        mcp_server.update_knowledge(lesson["id"], json.dumps({"summary": "edited"}),
                                    expected_version=_version(eng, lesson["id"])),
        mcp_server.update_knowledge(decision["id"], json.dumps({"choice": "main"}),
                                    expected_version=_version(eng, decision["id"])),
        mcp_server.update_knowledge(playbook["id"], json.dumps({"title": "edited"}),
                                    expected_version=_version(eng, playbook["id"])),
        mcp_server.update_knowledge(lesson["id"], json.dumps({"pinned": False}),
                                    expected_version=_version(eng, lesson["id"])),
        mcp_server.archive_knowledge(lesson["id"], expected_version=_version(eng, lesson["id"])),
        mcp_server.archive_knowledge(decision["id"], expected_version=_version(eng, decision["id"])),
        mcp_server.archive_knowledge(playbook["id"], expected_version=_version(eng, playbook["id"])),
        mcp_server.merge_knowledge(lesson["id"], other["id"], primary_expected_version=1,
                                   secondary_expected_version=1),
        mcp_server.merge_knowledge(other["id"], lesson["id"], primary_expected_version=1,
                                   secondary_expected_version=1),
        mcp_server.manage_playbook("archive", playbook["id"], expected_version=1),
        mcp_server.manage_playbook("delete", playbook["id"], dry_run=False, confirm=True, expected_version=1),
    ]
    for call in calls:
        result = _json(_run(call))
        assert result["error"] == "pinned_entry", result
        assert result["proposal"]["supersedes"] in {lesson["id"], decision["id"], playbook["id"]}
        assert result["proposal"]["tool"] in {"add_lesson", "add_decision", "add_playbook"}
    # the version is not even needed to learn that the entry is pinned
    assert _json(_run(mcp_server.update_knowledge(lesson["id"], json.dumps({"summary": "x"}))))["error"] == "pinned_entry"
    assert _store(eng.root) == before
    # a playbook content update is a proposal: the pinned playbook itself stays as it is
    pb_before = eng._read_playbook_by_id(playbook["id"])
    proposal = _json(_run(mcp_server.manage_playbook("update", playbook["id"], title="edited", expected_version=1)))
    assert proposal["status"] == "pending" and proposal["pending_supersedes"] == playbook["id"]
    assert eng._read_playbook_by_id(playbook["id"]) == pb_before


def test_mcp_review_apply_text_cannot_archive_a_pinned_entry(eng):
    lesson = _lesson(eng, "Pinned lesson that an outline review tries to archive")
    assert run_pin([lesson["id"]]) == 0
    _run(mcp_server.review_staging(action="apply_text", review_text=json.dumps({"archive": [{"id": lesson["id"]}]})))
    row = _row(eng, lesson["id"])
    assert row["status"] == "active" and row["pinned"] is True


def test_mcp_check_anchors_does_not_demote_a_pinned_entry(eng, tmp_path, monkeypatch):
    from piia_engram import freshness_anchors

    monkeypatch.setattr(freshness_anchors, "read_project_id", lambda root: "example.org/team/repo")
    pinned = _lesson(eng, "Pinned lesson backed by a dependency anchor")
    control = _lesson(eng, "Unpinned lesson backed by the same dependency anchor")
    for item in (pinned, control):
        eng.confirm_knowledge(item["id"], by="anchor", anchor_ref="dep:left-pad",
                              anchor_project_id="example.org/team/repo")
    assert run_pin([pinned["id"]]) == 0
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
    _run(mcp_server.check_anchors(str(repo)))
    assert _row(eng, control["id"])["tier"] == "staging"  # the check did run
    row = _row(eng, pinned["id"])
    assert row["tier"] == "verified" and row["pinned"] is True


# ---------------------------------------------------------------------------
# revision proposals that supersede a pinned entry always wait for the Owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["default", "strict"])
def test_supersede_proposals_for_pinned_entries_go_to_review(eng, monkeypatch, mode):
    lesson, decision, playbook = _pinned_trio(eng)
    if mode == "strict":
        _strict(monkeypatch)

    _run(mcp_server.add_lesson(summary="Tag releases from the release branch after the freeze",
                               domain="workflow", supersedes=lesson["id"],
                               supersedes_expected_version=_version(eng, lesson["id"]), user_confirmed=True))
    _run(mcp_server.add_decision(question="Which branch do releases come from now?", choice="a release tag",
                                 supersedes=decision["id"],
                                 supersedes_expected_version=_version(eng, decision["id"]), user_confirmed=True))
    _run(mcp_server.add_playbook(title="Cut a release with the release tool", triggers="release",
                                 steps_json=json.dumps([{"action": "run the tool"}]), supersedes=playbook["id"],
                                 supersedes_expected_version=1, user_confirmed=True))

    lessons = json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    decisions = json.loads((eng.root / "knowledge" / "decisions.json").read_text(encoding="utf-8"))
    proposals = [r for r in lessons + decisions if r.get("pending_supersedes")]
    assert {r["pending_supersedes"] for r in proposals} == {lesson["id"], decision["id"]}
    assert all(r["tier"] == "staging" for r in proposals)
    pb_rows = [eng._read_playbook_by_id(p.stem) for p in (eng.root / "playbooks").glob("*.json")
               if not p.name.startswith("_")]
    pb_proposal = [r for r in pb_rows if r and r.get("pending_supersedes") == playbook["id"]]
    assert len(pb_proposal) == 1 and pb_proposal[0]["tier"] == "staging"
    for item in (lesson, decision, playbook):  # the pinned entries are untouched
        row = _row(eng, item["id"])
        assert row["pinned"] is True and recall_policy.classify(row, eng._recall_supersede_index()).state == "trusted"


def test_an_auto_detected_revision_of_a_pinned_decision_waits_for_review(eng):
    decision = _decision(eng, "Which database stores the audit trail?", "the primary database")
    assert run_pin([decision["id"]]) == 0
    new = eng.add_decision({"question": "Which database stores the audit trail?", "choice": "a separate log store"})
    row = _row(eng, new["id"])
    assert row["tier"] == "staging" and row.get("pending_supersedes") == decision["id"]
    assert eng._recall_supersede_index().successor(decision["id"]) == ""


def test_mcp_batch_review_cannot_approve_a_revision_of_a_pinned_entry(eng):
    decision = _decision(eng, "Which region hosts the backups?", "the home region")
    assert run_pin([decision["id"]]) == 0
    proposal = eng.add_decision({"question": "Which region hosts the backups now?", "choice": "two regions",
                                 "supersedes": decision["id"]})
    before = _store(eng.root)
    result = _json(_run(mcp_server.review_staging(
        action="batch", actions_json=json.dumps([{"id": proposal["id"], "action": "approve"}]),
        dry_run=False, confirm=True)))
    assert result["error"] == "local_review_only"  # deciding proposals is local only over MCP
    assert _store(eng.root) == before


def _marks(tmp_path: Path, marks: list[dict]) -> Path:
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(marks), encoding="utf-8")
    return path


def test_owner_approval_supersedes_the_pinned_entry_and_unpins_it(eng, tmp_path, capsys):
    lesson, decision, playbook = _pinned_trio(eng)
    new_lesson = eng.add_lesson({"summary": "Tag releases from signed tags only", "domain": "workflow",
                                 "supersedes": lesson["id"]})
    new_decision = eng.add_decision({"question": "Which branch do releases come from today?",
                                     "choice": "signed tags", "supersedes": decision["id"]})
    new_playbook = eng.add_playbook({"title": "Cut a signed release", "steps": [{"action": "sign"}]},
                                    _update_proposal_of=playbook["id"])
    for item in (new_lesson, new_decision):
        assert _row(eng, item["id"])["tier"] == "staging"
    assert eng._read_playbook_by_id(new_playbook["id"])["tier"] == "staging"
    capsys.readouterr()

    marks = [{"id": i["id"], "mark": "approve"} for i in (new_lesson, new_decision, new_playbook)]
    assert review_cli.run_apply([str(_marks(tmp_path, marks)), "--operator", "owner", "--yes"]) == 0
    capsys.readouterr()

    index = eng._recall_supersede_index()
    for old, new in ((lesson, new_lesson), (decision, new_decision)):
        row = _row(eng, old["id"])
        assert recall_policy.classify(row, index).state == recall_policy.SUPERSEDED
        assert index.successor(old["id"]) == new["id"]
        assert "pinned" not in row
    old_pb = eng._read_playbook_by_id(playbook["id"])
    assert old_pb["status"] != "active" and "pinned" not in old_pb
    unpinned = {e["id"] for e in _audit(eng.root) if e.get("resource") == "pin/auto_unpin"}
    assert unpinned == {lesson["id"], decision["id"], playbook["id"]}


def test_owner_archive_of_a_pinned_entry_removes_the_pin(eng):
    lesson = _lesson(eng, "Pinned lesson the Owner archives locally")
    assert run_pin([lesson["id"]]) == 0
    eng.archive_knowledge(lesson["id"])  # a local (non-MCP) call
    row = _row(eng, lesson["id"])
    assert row["status"] == "outdated" and "pinned" not in row
    assert any(e.get("resource") == "pin/auto_unpin" and e["id"] == lesson["id"] for e in _audit(eng.root))


# ---------------------------------------------------------------------------
# exemptions: lifecycle archive, capacity, imports
# ---------------------------------------------------------------------------


def test_lifecycle_selection_skips_pinned_rows():
    from piia_engram import lifecycle

    report = {"proposals": [
        {"id": "a", "proposal": lifecycle.PROPOSAL_ARCHIVE, "tier": "", "pinned": True},
        {"id": "b", "proposal": lifecycle.PROPOSAL_ARCHIVE, "tier": ""},
    ]}
    assert lifecycle.select_archive_candidate_ids(report) == ["b"]


def test_lifecycle_apply_leaves_a_pinned_lesson(eng):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    lesson = _lesson(eng, "Pinned lesson that is old and never read")
    assert run_pin([lesson["id"]]) == 0
    result = apply_lifecycle_archive(eng, ids=[lesson["id"]], dry_run=False, confirm=True)
    assert result["items"][0]["outcome"] == "protected"
    row = _row(eng, lesson["id"])
    assert row["tier"] == "verified" and row["pinned"] is True


def test_capacity_full_of_pinned_rows_is_safe(eng, monkeypatch):
    monkeypatch.setenv("ENGRAM_CAP_SOFT", "2")
    monkeypatch.setenv("ENGRAM_CAP_HARD", "2")
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "1")
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_CEILING", "1")
    monkeypatch.setenv("ENGRAM_RETIRED_MAX", "1")
    monkeypatch.setenv("ENGRAM_REVIEW_MIN_STAY_DAYS", "0")
    pinned = [_lesson(eng, f"Pinned capacity lesson number {n} about disk quotas") for n in range(2)]
    for item in pinned:
        assert run_pin([item["id"]]) == 0

    results = [eng.add_lesson({"summary": f"New lesson {n} while the verified pool is full of pins",
                               "domain": "workflow"}) for n in range(4)]
    assert all(isinstance(r, dict) for r in results)
    rows = {r["id"]: r for r in json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))}
    for item in pinned:
        assert rows[item["id"]]["pinned"] is True and rows[item["id"]]["tier"] == "verified"
    archived = eng._read_overflow_archive("lesson")
    assert not {r.get("id") for r in archived} & {p["id"] for p in pinned}


def test_capacity_plan_result_never_touches_a_pinned_row():
    """Reviewed (pool V) rows are never moved by the capacity rules anyway; this
    guards the plan's result: a pinned row stays in ``rows`` and never shows up
    in ``archive``, even when every limit is exceeded around it."""
    from datetime import datetime, timezone

    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    old = "2000-01-01T00:00:00Z"
    rows = [{"id": f"r{n}", "status": "outdated", "tier": "verified", "retired_at": old} for n in range(3)]
    rows.append({"id": "p", "status": "active", "tier": "verified", "pinned": True, "pinned_at": old})
    after = [dict(r) for r in rows] + [{"id": "new", "status": "outdated", "tier": "verified"}]
    plan = capacity.plan_capacity([dict(r) for r in rows], after, kind="lesson", now=now,
                                  limits=capacity.Limits(soft_cap=1, hard_cap=1, review_queue_max=1,
                                                         review_queue_ceiling=1, r_max=1),
                                  ctx=capacity.CapacityContext())
    assert "p" in {r["id"] for r in plan.rows}
    assert "p" not in {r.get("id") for r, _reason in plan.archive}


def _backup(tmp_path: Path, name: str, lessons: list[dict], playbooks: list[dict] | None = None) -> Path:
    path = tmp_path / name
    data = {"schema_version": "1.0", "exported_at": "2026-10-01T00:00:00Z", "identity": {},
            "knowledge": {"lessons": lessons, "decisions": [], "playbooks": playbooks or []}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_import_merge_does_not_overwrite_a_pinned_entry(eng, tmp_path):
    lesson = _lesson(eng, "Pinned import lesson: keep the lockfile in git")
    playbook = _playbook(eng, "Pinned import playbook")
    assert run_pin([lesson["id"]]) == 0 and run_pin([playbook["id"]]) == 0
    incoming = [
        {**_row(eng, lesson["id"]), "detail": "changed elsewhere", "pinned": False},
        {"id": lesson["id"], "summary": "Same id, other text", "tier": "verified", "status": "active"},
        {"id": "imp-pinned", "summary": "An imported row that claims a pin", "tier": "verified",
         "status": "active", "pinned": True, "pinned_at": "2000-01-01T00:00:00Z"},
    ]
    pb_in = [{"id": playbook["id"], "title": "Other title, same id", "steps": [{"action": "x"}]}]
    path = _backup(tmp_path, "merge.json", incoming, pb_in)

    preview = eng.import_all(str(path), merge=True, dry_run=True)
    assert lesson["id"] in preview["pinned"]["protected"]["lessons"]
    assert playbook["id"] in preview["pinned"]["protected"]["playbooks"]
    assert preview["pinned"]["warning"]

    result = eng.import_all(str(path), merge=True, materialize_version_chain=True)
    assert lesson["id"] in result["pinned"]["protected"]["lessons"]
    rows = json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    mine = [r for r in rows if r.get("id") == lesson["id"]]
    assert len(mine) == 1 and mine[0]["pinned"] is True and mine[0]["status"] == "active"
    assert mine[0]["summary"] == "Pinned import lesson: keep the lockfile in git"
    claimed = [r for r in rows if r.get("id") == "imp-pinned"]
    assert claimed and "pinned" not in claimed[0]  # an import never brings a pin in
    pb = eng._read_playbook_by_id(playbook["id"])
    assert pb["title"] == "Pinned import playbook" and pb["pinned"] is True


def test_import_replace_keeps_pinned_entries_and_warns(eng, tmp_path):
    lesson = _lesson(eng, "Pinned lesson that a replace import must keep")
    plain = _lesson(eng, "Plain lesson that a replace import replaces")
    assert run_pin([lesson["id"]]) == 0
    incoming = [{"id": "fresh-1", "summary": "The only lesson in the backup", "tier": "verified",
                 "status": "active"},
                {"id": lesson["id"], "summary": "A backup copy with other text", "tier": "verified",
                 "status": "active"}]
    path = _backup(tmp_path, "replace.json", incoming)

    preview = eng.import_all(str(path), merge=False, dry_run=True)
    assert preview["pinned"]["protected"]["lessons"] == [lesson["id"]]
    assert "pinned" in preview["pinned"]["warning"].lower()

    result = eng.import_all(str(path), merge=False)
    assert result["pinned"]["protected"]["lessons"] == [lesson["id"]]
    rows = {r["id"]: r for r in json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))}
    assert rows[lesson["id"]]["pinned"] is True
    assert rows[lesson["id"]]["summary"] == "Pinned lesson that a replace import must keep"
    assert "fresh-1" in rows and plain["id"] not in rows


# ---------------------------------------------------------------------------
# recall and search
# ---------------------------------------------------------------------------


def test_pinned_first_is_stable():
    rows = [{"id": "a", "status": "active", "tier": "verified"},
            {"id": "b", "status": "active", "tier": "verified", "pinned": True},
            {"id": "c", "status": "active", "tier": "staging", "pinned": True},  # not trusted: no effect
            {"id": "d", "status": "active", "tier": "verified", "pinned": True}]
    assert [r["id"] for r in recall_policy.pinned_first(rows)] == ["b", "d", "a", "c"]
    assert [r["id"] for r in recall_policy.pinned_last(rows)] == ["a", "c", "b", "d"]


def test_relevant_lessons_put_pinned_first_and_keep_them_under_a_cap(eng):
    oldest = _lesson(eng, "Pinned oldest lesson about code review etiquette")
    for n in range(6):
        _lesson(eng, f"Newer lesson {n} about unrelated chores and errands")
    assert run_pin([oldest["id"]]) == 0
    result = eng.get_relevant_lessons(limit=2, _update_access=False)
    assert result[0]["id"] == oldest["id"]
    assert len(result) == 2


def test_resume_brief_and_cold_start_keep_pinned_entries(eng):
    pinned_lesson = _lesson(eng, "zqpinlesson keep the changelog in two languages")
    for n in range(6):
        _lesson(eng, f"Filler lesson number {n} about tidy commits")
    pinned_decision = _decision(eng, "zqpindecision which license applies?", "MIT")
    for n in range(8):
        _decision(eng, f"Filler decision {n} which colour is the button?", f"colour {n}")
    assert run_pin([pinned_lesson["id"]]) == 0 and run_pin([pinned_decision["id"]]) == 0

    brief = eng.get_resume_brief(token_budget=4000)
    text = json.dumps(brief, ensure_ascii=False)
    assert "zqpinlesson" in text and "zqpindecision" in text
    context, _omitted = eng.generate_context_report()
    assert "zqpindecision" in context


def test_search_is_not_polluted_by_pinned_entries(eng):
    pinned = _lesson(eng, "Pinned lesson about kubernetes ingress annotations")
    assert run_pin([pinned["id"]]) == 0
    _lesson(eng, "Prefer parameterized SQL queries over string building")
    result = eng.search_knowledge("parameterized SQL", scope="lessons")
    ids = [item["id"] for item in result["lessons"]]
    assert pinned["id"] not in ids and ids


def test_search_breaks_ties_with_the_pin(eng):
    first = _lesson(eng, "Cache invalidation note alpha")
    second = _lesson(eng, "Cache invalidation note gamma")
    query = "cache invalidation note"
    before = eng.search_knowledge(query, scope="lessons")["lessons"]
    scores = {item["id"]: item["_score"] for item in before}
    assert scores[first["id"]] == scores[second["id"]]  # equally relevant
    unpinned_order = [item["id"] for item in before]
    loser = unpinned_order[-1]
    assert run_pin([loser]) == 0
    after = [item["id"] for item in eng.search_knowledge(query, scope="lessons")["lessons"]]
    assert after[0] == loser


def test_memory_lens_marks_pinned_entries(eng):
    from piia_engram.context_preview import build_context_preview, render_context_preview_text

    lesson = _lesson(eng, "Pinned lesson shown in the memory lens preview")
    assert run_pin([lesson["id"]]) == 0
    preview = build_context_preview(eng)
    pinned_rows = [row for row in preview["knowledge"]["exposed"] if row.get("pinned")]
    assert pinned_rows
    text = render_context_preview_text(preview)
    assert "已钉住" in text or "pinned" in text


# ---------------------------------------------------------------------------
# review surfaces
# ---------------------------------------------------------------------------


def test_review_card_flags_a_pinned_target(eng, monkeypatch):
    decision = _decision(eng, "Which CI runs the nightly build?", "the hosted CI")
    assert run_pin([decision["id"]]) == 0
    _strict(monkeypatch)
    proposal = eng.add_decision({"question": "Which CI runs the nightly build now?", "choice": "self-hosted",
                                 "supersedes": decision["id"]})
    row = _row(eng, proposal["id"])
    assert row["pending_supersedes"] == decision["id"]
    text = "\n".join(review_cli._card(1, "decision", row, eng, {}))
    assert "SUPERSEDES" in text and "pinned" in text.lower()

    from piia_engram import review_interactive

    lines, _folded = review_interactive.card(1, 1, "decision", row, eng=eng, lookup={}, edges=[])
    card = "\n".join(lines)
    assert "钉住" in card or "pinned" in card.lower()


def test_management_view_shows_pinned_playbooks(eng):
    from piia_engram.management_view import build_management_view, render_management_text

    pinned = _playbook(eng, "Pinned playbook in the management view")
    _playbook(eng, "Plain playbook in the management view")
    assert run_pin([pinned["id"]]) == 0
    view = build_management_view(eng)
    flags = {item["id"]: item["pinned"] for item in view["playbooks"]["items"]}
    assert flags[pinned["id"]] is True and list(flags.values()).count(True) == 1
    assert "1 pinned" in render_management_text(view)


def test_review_show_prints_the_pin(eng, capsys):
    from piia_engram.cli_commands import _print_review_item

    lesson = _lesson(eng, "Pinned lesson printed by review show")
    assert run_pin([lesson["id"]]) == 0
    capsys.readouterr()
    _print_review_item("lesson", _row(eng, lesson["id"]))
    assert "pinned: yes" in capsys.readouterr().out


def test_a_restored_playbook_does_not_bring_its_pin_back(eng):
    playbook = _playbook(eng, "Pinned playbook the Owner deletes locally")
    assert run_pin([playbook["id"]]) == 0
    eng.delete_playbook(playbook["id"], dry_run=False, confirm=True)  # local, not MCP
    assert "pinned" not in eng._read_playbook_by_id(playbook["id"])
    version = eng._read_playbook_by_id(playbook["id"])["version"]
    restored = _json(_run(mcp_server.manage_playbook("restore", playbook["id"], dry_run=False, confirm=True,
                                                     expected_version=version)))
    assert restored["dry_run"] is False
    row = eng._read_playbook_by_id(playbook["id"])
    assert row["status"] == "active" and "pinned" not in row


# ---------------------------------------------------------------------------
# an MCP caller cannot approve a revision of a pinned entry by any route
# ---------------------------------------------------------------------------


def _proposals_for_pinned_trio(eng):
    lesson, decision, playbook = _pinned_trio(eng)
    new_lesson = eng.add_lesson({"summary": "Tag releases from signed tags only", "domain": "workflow",
                                 "supersedes": lesson["id"]})
    new_decision = eng.add_decision({"question": "Which branch do releases come from today?",
                                     "choice": "signed tags", "supersedes": decision["id"]})
    new_playbook = eng.add_playbook({"title": "Cut a signed release", "steps": [{"action": "sign"}]},
                                    _update_proposal_of=playbook["id"])
    for item in (new_lesson, new_decision):
        assert _row(eng, item["id"])["tier"] == "staging"
    assert eng._read_playbook_by_id(new_playbook["id"])["tier"] == "staging"
    return (lesson, decision, playbook), (new_lesson, new_decision, new_playbook)


def test_mcp_cannot_promote_a_revision_of_a_pinned_entry(eng, tmp_path, capsys):
    olds, news = _proposals_for_pinned_trio(eng)
    new_lesson, new_decision, new_playbook = news
    capsys.readouterr()
    before = _store(eng.root)

    for item in (new_lesson, new_decision):  # update_knowledge: tier -> verified
        result = _json(_run(mcp_server.update_knowledge(
            item["id"], json.dumps({"tier": "verified"}), expected_version=_version(eng, item["id"]))))
        assert result["error"] == "local_review_only", result
    # the outline review's promote list
    _run(mcp_server.review_staging(action="apply_text", review_text=json.dumps(
        {"promote": [{"id": i["id"]} for i in news], "archive": []})))
    # batch approve (playbook route too)
    batch = _json(_run(mcp_server.review_staging(
        action="batch", actions_json=json.dumps([{"id": i["id"], "action": "approve"} for i in news]),
        dry_run=False, confirm=True)))
    assert batch["error"] == "local_review_only"
    assert _store(eng.root) == before
    # the playbook approval primitive refuses on behalf of an MCP caller as well
    from piia_engram import write_provenance

    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        assert eng.approve_playbook(new_playbook["id"])["status"] == "local_review_only"
    assert _store(eng.root) == before

    # the Owner's local review approves the same proposals
    marks = [{"id": i["id"], "mark": "approve"} for i in news]
    assert review_cli.run_apply([str(_marks(tmp_path, marks)), "--operator", "owner", "--yes"]) == 0
    index = eng._recall_supersede_index()
    for old in olds[:2]:
        row = _row(eng, old["id"])
        assert recall_policy.classify(row, index).state == recall_policy.SUPERSEDED and "pinned" not in row
    old_pb = eng._read_playbook_by_id(olds[2]["id"])
    assert old_pb["status"] != "active" and "pinned" not in old_pb


def test_owner_interactive_review_approves_a_revision_of_a_pinned_entry(eng, monkeypatch):
    import io

    from piia_engram import i18n, review_interactive

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(i18n, "_runtime_lang", "en")
    decision = _decision(eng, "Which region hosts the archive?", "the home region")
    assert run_pin([decision["id"]]) == 0
    proposal = eng.add_decision({"question": "Which region hosts the archive now?", "choice": "two regions",
                                 "supersedes": decision["id"]})
    assert _row(eng, proposal["id"])["tier"] == "staging"
    keys = _Tty("a\ny\n")
    screen = _Tty()
    assert review_interactive.run(["--operator", "owner"], stdin=keys, stdout=screen) == 0
    index = eng._recall_supersede_index()
    assert index.successor(decision["id"]) == proposal["id"]
    assert "pinned" not in _row(eng, decision["id"])


# ---------------------------------------------------------------------------
# a supersedes target must be applicable: same project, not archived
# ---------------------------------------------------------------------------


def test_supersedes_targets_in_another_project_or_archived_are_refused(eng, tmp_path):
    other_project = str(tmp_path / "other-project")
    lesson_far = eng.add_lesson({"summary": "A lesson that belongs to another project", "domain": "workflow",
                                 "tier": "verified", "project_folder": other_project})
    lesson_old = _lesson(eng, "A lesson the Owner archived some time ago")
    eng.archive_knowledge(lesson_old["id"])
    decision_old = _decision(eng, "Which mirror do we use?", "the old mirror")
    eng.archive_knowledge(decision_old["id"])
    pb_far = eng.add_playbook({"title": "Project-only release steps", "steps": [{"action": "x"}],
                               "scope_type": "project", "project_folder": other_project})
    before = _store(eng.root)

    cases = [
        (mcp_server.add_lesson(summary="Revise the far lesson", supersedes=lesson_far["id"],
                               supersedes_expected_version=1, user_confirmed=True), "scope_mismatch"),
        (mcp_server.add_lesson(summary="Revise the archived lesson", supersedes=lesson_old["id"],
                               supersedes_expected_version=1, user_confirmed=True), "archived"),
        (mcp_server.add_decision(question="Which mirror do we use now?", choice="the new mirror",
                                 supersedes=decision_old["id"], supersedes_expected_version=1,
                                 user_confirmed=True), "archived"),
        (mcp_server.add_playbook(title="Global release steps", triggers="release",
                                 steps_json=json.dumps([{"action": "y"}]), supersedes=pb_far["id"],
                                 supersedes_expected_version=1, user_confirmed=True), "scope_mismatch"),
    ]
    for call, reason in cases:
        result = _json(_run(call))
        assert result["error"] == "supersedes_target_not_applicable", result
        assert result["reason"] == reason
    assert _store(eng.root) == before


# ---------------------------------------------------------------------------
# import CLI text names the pinned entries it kept
# ---------------------------------------------------------------------------


def test_import_cli_text_lists_pinned_entries(eng, tmp_path):
    from piia_engram.cli_commands import _render_import_result_text

    lesson = _lesson(eng, "Pinned lesson the import CLI reports")
    assert run_pin([lesson["id"]]) == 0
    path = _backup(tmp_path, "cli.json", [{"id": "fresh-2", "summary": "Backup lesson", "tier": "verified",
                                            "status": "active"}])
    for merge in (True, False):
        for dry_run in (True, False):
            payload = eng.import_all(str(path), merge=merge, dry_run=dry_run)
            text = _render_import_result_text(payload)
            if merge:  # nothing in the backup matches the pinned lesson
                assert "pinned" not in text.lower()
            else:
                assert "pinned" in text.lower() and lesson["id"] in text
                assert "kept" in text.lower()
    path = _backup(tmp_path, "cli2.json", [{"id": lesson["id"], "summary": "Other text", "tier": "verified",
                                             "status": "active"}])
    text = _render_import_result_text(eng.import_all(str(path), merge=True, dry_run=True))
    assert lesson["id"] in text and "skipped" in text.lower()


def test_replace_import_keeps_pinned_rows_in_place(eng, tmp_path):
    first = _lesson(eng, "First local lesson before the pin")
    pinned = _lesson(eng, "Pinned lesson in the middle of the file")
    _lesson(eng, "Third local lesson after the pin")
    assert run_pin([pinned["id"]]) == 0
    incoming = [{"id": f"in-{n}", "summary": f"Backup lesson number {n}", "tier": "verified",
                 "status": "active"} for n in range(3)]
    eng.import_all(str(_backup(tmp_path, "order.json", incoming)), merge=False)
    ids = [r["id"] for r in json.loads((eng.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
           if not r.get("snapshot_of")]
    assert ids == ["in-0", pinned["id"], "in-1", "in-2"]
    assert first["id"] not in ids


# ---------------------------------------------------------------------------
# a pin never beats relevance
# ---------------------------------------------------------------------------


def _search_rows():
    strong = {"id": "strong", "summary": "kubernetes ingress annotations guide", "detail": "",
              "status": "active", "tier": "verified"}
    weak = {"id": "weak", "summary": "kubernetes notes", "detail": "", "status": "active",
            "tier": "verified", "pinned": True}
    return strong, weak


def test_keyword_search_ranks_a_strong_match_above_a_weak_pinned_one(eng):
    strong, weak = _search_rows()
    query = "kubernetes ingress annotations"
    ranked = eng._rank_scope([weak, strong], query.split(), query, 10, None)
    assert [v["id"] for v in ranked] == ["strong", "weak"]
    assert ranked[0]["_score"] > ranked[1]["_score"]


def test_hybrid_search_ranks_a_strong_match_above_a_weak_pinned_one(eng):
    strong, weak = _search_rows()

    class _Index:
        def fts_search(self, query, limit=50):
            return ["strong", "weak"]

        def vector_search(self, query, limit=50):
            return ["strong", "weak"]

    query = "kubernetes ingress annotations"
    ranked = eng._rank_scope([weak, strong], query.split(), query, 10, _Index())
    assert [v["id"] for v in ranked] == ["strong", "weak"]


# ---------------------------------------------------------------------------
# conflicts resolve --action supersede audits the unpin as superseded
# ---------------------------------------------------------------------------


def test_conflict_supersede_unpins_with_reason_superseded(eng, capsys):
    from piia_engram.setup_wizard import run_conflicts

    rows = [
        {"id": "conflict-a", "question": "which release gate should Engram use", "choice": "manual owner approval",
         "domain": "release", "status": "active", "tier": "verified", "timestamp": "2026-10-01T00:00:00Z"},
        {"id": "conflict-b", "question": "which release gate should Engram use", "choice": "automated pipeline gate",
         "domain": "release", "status": "active", "tier": "verified", "timestamp": "2026-10-01T00:00:00Z"},
    ]
    eng._write_entries(eng._knowledge_dir / "decisions.json", rows, "decision")
    assert run_pin(["conflict-a"]) == 0
    capsys.readouterr()
    assert run_conflicts(["resolve", "conflict-a", "conflict-b", "--action", "supersede", "--keep", "conflict-b",
                          "--commit", "--yes", "--json"]) == 0
    events = [e for e in _audit(eng.root) if e.get("resource") == "pin/auto_unpin" and e["id"] == "conflict-a"]
    assert [e["reason"] for e in events] == ["superseded"]
    assert "pinned" not in _row(eng, "conflict-a")


def test_supersede_scope_reasons_name_the_fix(eng, tmp_path):
    proj_a, proj_b = str(tmp_path / "proj-a"), str(tmp_path / "proj-b")
    global_lesson = _lesson(eng, "A global lesson about commit messages")
    lesson_a = eng.add_lesson({"summary": "A project lesson about the build in project A", "domain": "workflow",
                               "tier": "verified", "project_folder": proj_a})
    before = _store(eng.root)

    # a project proposal may not supersede a global entry
    result = _json(_run(mcp_server.add_lesson(summary="Project revision of the global lesson",
                                              project_folder=proj_a, supersedes=global_lesson["id"],
                                              supersedes_expected_version=1, user_confirmed=True)))
    assert result["error"] == "supersedes_target_not_applicable" and result["reason"] == "scope_mismatch"
    assert "global" in result["message"].lower()
    # a global proposal may not supersede a project entry
    result = _json(_run(mcp_server.add_lesson(summary="Global revision of the project lesson",
                                              supersedes=lesson_a["id"], supersedes_expected_version=1,
                                              user_confirmed=True)))
    assert result["error"] == "supersedes_target_not_applicable" and result["reason"] == "scope_mismatch"
    assert "same project" in result["message"].lower()
    # two different projects
    result = _json(_run(mcp_server.add_lesson(summary="Project B revision of the project A lesson",
                                              project_folder=proj_b, supersedes=lesson_a["id"],
                                              supersedes_expected_version=1, user_confirmed=True)))
    assert result["error"] == "supersedes_target_not_applicable" and result["reason"] == "different_project"
    assert _store(eng.root) == before


def test_a_lifecycle_archived_target_counts_as_archived(eng):
    lesson = _lesson(eng, "A lesson the lifecycle archive moved to the archived tier")
    eng.soft_archive_knowledge_tier(lesson["id"], allow_verified=True)
    row = _row(eng, lesson["id"])
    assert row["status"] == "active" and row["tier"] == "archived"
    before = _store(eng.root)
    result = _json(_run(mcp_server.add_lesson(summary="Revision of the lifecycle-archived lesson",
                                              supersedes=lesson["id"], supersedes_expected_version=1,
                                              user_confirmed=True)))
    assert result["error"] == "supersedes_target_not_applicable" and result["reason"] == "archived"
    assert _store(eng.root) == before


# ---------------------------------------------------------------------------
# imports never supersede a pinned entry (MCP and local)
# ---------------------------------------------------------------------------


def _edge_backup(tmp_path: Path, pinned_id: str) -> Path:
    path = tmp_path / "edges.json"
    data = {"schema_version": "1.0", "exported_at": "2026-10-01T00:00:00Z", "identity": {},
            "knowledge": {"lessons": [{"id": "imp-x", "summary": "Imported lesson that claims to replace a pin",
                                       "tier": "verified", "status": "active"}],
                          "decisions": [], "playbooks": [],
                          "relations": [{"src": "imp-x", "rel": "supersedes", "dst": pinned_id},
                                        {"src": "imp-x", "rel": "led_to", "dst": pinned_id}]}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _assert_pin_survived(eng, pinned_id: str) -> None:
    row = _row(eng, pinned_id)
    assert row["pinned"] is True
    assert eng._recall_supersede_index().successor(pinned_id) == ""
    assert recall_policy.classify(row, eng._recall_supersede_index()).state == recall_policy.TRUSTED
    edges = RelationStore(eng.root).all_edges()
    assert not [e for e in edges if e["rel"] == "supersedes" and e["dst"] == pinned_id]


def test_mcp_import_cannot_supersede_a_pinned_entry(eng, tmp_path):
    pinned = _lesson(eng, "Pinned lesson an imported edge targets")
    assert run_pin([pinned["id"]]) == 0
    path = _edge_backup(tmp_path, pinned["id"])

    preview = _json(_run(mcp_server.import_engram(input_path=str(path), merge=True, dry_run=True)))
    assert pinned["id"] in preview["pinned"]["protected"]["lessons"]
    assert {"src": "imp-x", "dst": pinned["id"]} in preview["pinned"]["dropped_edges"]

    # applying an import over MCP is refused outright (local only); nothing changes
    before = _store(eng.root)
    result = _json(_run(mcp_server.import_engram(input_path=str(path), merge=True)))
    assert result["error"] == "local_only"
    assert _store(eng.root) == before
    _assert_pin_survived(eng, pinned["id"])
    # the local import brings the rest in and drops the link
    local = eng.import_all(str(path), merge=True)
    assert {"src": "imp-x", "dst": pinned["id"]} in local["pinned"]["dropped_edges"]
    _assert_pin_survived(eng, pinned["id"])
    assert _row(eng, "imp-x") is not None


def test_local_import_cannot_supersede_a_pinned_entry(eng, tmp_path, capsys):
    from piia_engram.cli_commands import _render_import_result_text

    pinned = _decision(eng, "Which formatter is used?", "the project formatter")
    assert run_pin([pinned["id"]]) == 0
    path = _edge_backup(tmp_path, pinned["id"])
    for merge in (True, False):
        result = eng.import_all(str(path), merge=merge)
        assert {"src": "imp-x", "dst": pinned["id"]} in result["pinned"]["dropped_edges"]
        text = _render_import_result_text(result)
        assert "imp-x" in text and pinned["id"] in text and "dropped" in text.lower()
        _assert_pin_survived(eng, pinned["id"])


def test_import_that_would_promote_a_revision_of_a_pinned_entry_is_refused(eng, tmp_path):
    pinned = _lesson(eng, "Pinned lesson a replace import would supersede by promotion")
    assert run_pin([pinned["id"]]) == 0
    proposal = eng.add_lesson({"summary": "Pending revision of the pinned lesson", "domain": "workflow",
                               "supersedes": pinned["id"]})
    stored = _row(eng, proposal["id"])
    assert stored["tier"] == "staging" and stored["pending_supersedes"] == pinned["id"]
    promoted = {**stored, "tier": "verified", "memory_state": "verified", "approval_status": "approved"}
    path = tmp_path / "promote.json"
    path.write_text(json.dumps({"schema_version": "1.0", "identity": {}, "knowledge": {
        "lessons": [_row(eng, pinned["id"]), promoted], "decisions": [], "playbooks": []}}), encoding="utf-8")
    before = _store(eng.root)
    assert _json(_run(mcp_server.import_engram(input_path=str(path), merge=False)))["error"] == "local_only"
    result = eng.import_all(str(path), merge=False)  # the Owner's local import refuses it too
    assert result["error"] == "pinned_target", result
    assert _store(eng.root) == before
    _assert_pin_survived(eng, pinned["id"])


# ---------------------------------------------------------------------------
# onboard accept never approves a revision proposal
# ---------------------------------------------------------------------------


def test_onboard_accept_refuses_a_playbook_revision_proposal(eng):
    playbook = _playbook(eng, "Pinned playbook an onboard accept tries to replace")
    assert run_pin([playbook["id"]]) == 0
    proposal = eng.add_playbook({"title": "Replacement playbook with an anchor", "steps": [{"action": "x"}],
                                 "provenance": {"anchor_ref": "file:README.md", "confirmation_source": "anchor"}},
                                _update_proposal_of=playbook["id"], _allow_internal_provenance=True)
    assert eng._read_playbook_by_id(proposal["id"])["tier"] == "staging"
    before = _store(eng.root)
    result = _json(_run(mcp_server.onboard_accept(proposal["id"])))
    assert result["error"] == "local_review_only", result
    assert eng.accept_onboard_candidate(proposal["id"])["error"] == "revision_proposal"  # local too
    assert _store(eng.root) == before

    plain = eng.add_playbook({"title": "Plain onboard candidate playbook", "steps": [{"action": "y"}],
                              "tier": "staging",
                              "provenance": {"anchor_ref": "file:README.md", "confirmation_source": "anchor"}},
                             _allow_internal_provenance=True)
    accepted = _json(_run(mcp_server.onboard_accept(plain["id"])))
    assert "error" not in accepted, accepted
    assert eng._read_playbook_by_id(plain["id"])["tier"] == "verified"


def test_apply_review_reports_why_a_promotion_did_not_happen(eng):
    decision = _decision(eng, "Which CI image is used?", "the stock image")
    assert run_pin([decision["id"]]) == 0
    proposal = eng.add_decision({"question": "Which CI image is used now?", "choice": "a slim image",
                                 "supersedes": decision["id"]})
    from piia_engram import write_provenance

    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        refused = eng.apply_review({"promote": [{"id": proposal["id"]}], "archive": []})
    assert refused["error"] == "local_review_only" and refused["promoted"] == 0
    # locally, the review result says why a promotion did not happen
    result = eng.apply_review({"promote": [{"id": "no-such-id"}], "archive": []})
    assert result["promoted"] == 0
    assert "promote no-such-id: not_found" in result["errors"]
