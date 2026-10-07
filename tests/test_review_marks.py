"""``engram review apply`` marks: supersede, reject reasons, version guards, receipts.

* ``supersede:<old id>`` approves a pending proposal and records that it
  replaces an approved entry (lesson / decision: a ``supersedes`` edge, so
  recall shows only the new one; playbook: the old one is archived);
* the target must exist, be trusted, be the same kind and scope, not be the
  proposal and not close a cycle; otherwise the item fails and nothing is written;
* a reject mark may carry the Owner's ``reason``, cleaned and capped, kept in the run's
  receipt only; the tombstone stays text-free;
* a marks file without the new fields applies exactly as before;
* ``expected_version`` skips an item that changed after it was reviewed;
* a run that stops part-way still leaves a receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import recall_policy
from piia_engram import review_cli
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.staging_review import batch_review_staging


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    return Engram(root=root)


def _approve(eng: Engram, item_id: str) -> None:
    result = batch_review_staging(eng, [{"id": item_id, "action": "approve"}], dry_run=False, confirm=True)
    assert result["counts"]["applied"] == 1, result


def _row(eng: Engram, item_id: str) -> dict:
    return eng._find_item_by_id(item_id)[1]


def _marks(tmp_path: Path, marks: list[dict]) -> Path:
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(marks), encoding="utf-8")
    return path


def _apply(tmp_path: Path, capsys, marks: list[dict], *, yes: bool = True, code: int = 0) -> dict:
    capsys.readouterr()
    args = [str(_marks(tmp_path, marks))] + (["--operator", "owner", "--yes"] if yes else [])
    assert review_cli.run_apply(args) == code
    return json.loads(capsys.readouterr().out)


def _knowledge(root: Path) -> dict[str, str]:
    """Hash of every knowledge and playbook file (an applying run also touches session files)."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for sub in ("knowledge", "playbooks") for p in sorted((root / sub).rglob("*")) if p.is_file()
    }


def _tombstones(root: Path) -> list[dict]:
    path = root / "knowledge" / "tombstones.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _receipts(root: Path) -> list[dict]:
    path = root / "audit.log"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("action") == "owner_cli"]


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


def test_supersede_mark_links_a_lesson_after_a_dry_run(eng, tmp_path, capsys):
    old = eng.add_lesson({"summary": "Store secrets in the CI settings page", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Store secrets in the CI secret store with rotation", "domain": "type:lesson"})
    marks = [{"id": new["id"], "mark": f"supersede:{old['id']}"}]
    before = _knowledge(eng.root)

    dry = _apply(tmp_path, capsys, marks, yes=False)
    assert dry["status"] == "dry_run" and dry["items"][0]["status"] == "planned"
    assert _knowledge(eng.root) == before

    applied = _apply(tmp_path, capsys, marks)

    assert applied["counts"]["supersede"] == 1 and applied["counts"]["supersede_failed"] == 0
    assert applied["items"] == [{"id": new["id"], "action": "supersede", "status": "applied", "target": old["id"],
                                 "phase": 2}]
    assert _row(eng, new["id"])["tier"] == "verified"
    assert "pending_supersedes" not in _row(eng, new["id"])
    index = eng._recall_supersede_index()
    assert index.successor(old["id"]) == new["id"]
    assert recall_policy.classify(_row(eng, old["id"]), index).state == recall_policy.SUPERSEDED


def test_supersede_mark_links_a_decision(eng, tmp_path, capsys):
    old = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    _approve(eng, old["id"])
    new = eng.add_decision({"question": "Which store holds shared build caches?", "choice": "the object store"})
    assert _row(eng, new["id"])["tier"] == "staging"

    applied = _apply(tmp_path, capsys, [{"id": new["id"], "mark": f"supersede:{old['id']}"}])

    assert applied["counts"]["supersede"] == 1
    edges = [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]
    assert (new["id"], old["id"]) in edges


def test_supersede_mark_retires_the_old_playbook(eng, tmp_path, capsys):
    old = eng.add_playbook({"title": "Rotate the signing key by hand",
                            "steps": [{"action": "Revoke the old key"}, {"action": "Mail the new key"}]})
    _approve(eng, old["id"])
    new = eng.add_playbook({"title": "Key rollover through the release tool",
                            "steps": [{"action": "Run the rotate command"}, {"action": "Publish the new key"}]})
    assert eng._read_playbook_by_id(new["id"])["tier"] == "staging"

    applied = _apply(tmp_path, capsys, [{"id": new["id"], "mark": f"supersede:{old['id']}"}])

    assert applied["counts"]["supersede"] == 1
    assert eng._read_playbook_by_id(new["id"])["tier"] == "verified"
    assert eng._read_playbook_by_id(old["id"])["status"] != "active"


def test_supersede_mark_to_a_bad_target_fails_without_writing(eng, tmp_path, capsys):
    pending = eng.add_lesson({"summary": "Another proposal still waiting", "domain": "type:lesson"})
    rule = eng.add_lesson({"summary": "Never cache build output", "domain": "type:rule"})
    _approve(eng, rule["id"])
    news = [eng.add_lesson({"summary": f"Cache the dependency layer between CI runs, variant {n}",
                            "domain": "type:lesson"})["id"] for n in range(4)]
    before = _knowledge(eng.root)

    applied = _apply(tmp_path, capsys, [
        {"id": news[0], "mark": "supersede:nosuchid0001"},
        {"id": news[1], "mark": f"supersede:{pending['id']}"},
        {"id": news[2], "mark": f"supersede:{news[2]}"},
        {"id": news[3], "mark": f"supersede:{rule['id']}"},
    ], code=1)  # every mark failed

    assert [i["status"] for i in applied["items"]] == [
        "target_not_found", "target_not_trusted", "self", "type_mismatch"]
    assert applied["counts"]["supersede_failed"] == 4 and applied["counts"]["applied"] == 0
    assert _knowledge(eng.root) == before


def test_dry_run_reports_a_bad_supersede_without_writing(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Use one lockfile per workspace", "domain": "type:lesson"})
    before = _knowledge(eng.root)

    payload = _apply(tmp_path, capsys, [{"id": row["id"], "mark": f"supersede:{row['id']}"}], yes=False)

    assert payload["status"] == "dry_run"
    assert payload["items"] == [{"id": row["id"], "action": "supersede", "status": "self", "target": row["id"],
                                 "phase": 2}]
    assert _knowledge(eng.root) == before


# ---------------------------------------------------------------------------
# reasons, versions, validation
# ---------------------------------------------------------------------------


def test_reject_reason_goes_to_the_receipt_not_the_tombstone(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Skip the changelog for small fixes", "domain": "type:lesson"})

    applied = _apply(tmp_path, capsys,
                     [{"id": row["id"], "mark": "reject", "reason": "no\x1b[2J: every fix\nis logged"}])

    (stone,) = _tombstones(eng.root)
    assert stone["via"] == "cli:owner" and "reason" not in stone
    assert "every fix" not in (eng.root / "knowledge" / "tombstones.jsonl").read_text(encoding="utf-8")
    (receipt,) = _receipts(eng.root)
    assert receipt["reject_reasons"] == {row["id"]: "no [2J: every fix is logged"}
    assert applied["items"][0]["reason"] == "no [2J: every fix is logged"


def test_old_format_marks_apply_exactly_as_before(eng, tmp_path, capsys):
    keep = eng.add_lesson({"summary": "Keep this proposal", "domain": "t"})
    drop = eng.add_lesson({"summary": "Drop this proposal", "domain": "t"})
    relabel = eng.add_lesson({"summary": "Relabel this approved entry", "domain": "feedback"})
    _approve(eng, relabel["id"])

    applied = _apply(tmp_path, capsys, [{"id": keep["id"], "mark": "approve"},
                                        {"id": drop["id"], "mark": "reject"},
                                        {"id": relabel["id"], "mark": "edit-type:rule"}])

    assert applied["status"] == "applied"
    assert applied["items"] == [{"id": keep["id"], "action": "approve", "status": "applied", "phase": 1},
                                {"id": drop["id"], "action": "reject", "status": "applied", "phase": 1},
                                {"id": relabel["id"], "action": "edit-type", "from": None, "to": "rule",
                                 "status": "applied", "phase": "edit"}]
    assert applied["order"] == [keep["id"], drop["id"], relabel["id"]]
    assert applied["edit_type_failed"] == []
    counts = applied["counts"]
    assert (counts["requested"], counts["approve"], counts["reject"], counts["planned"], counts["applied"],
            counts["noop"], counts["failed"], counts["edit_type"], counts["edit_type_failed"]) == (
        2, 1, 1, 2, 2, 0, 0, 1, 0)
    assert counts["supersede"] == 0 and counts["supersede_failed"] == 0
    assert _row(eng, keep["id"])["tier"] == "verified"
    assert _row(eng, drop["id"])["status"] == "outdated"
    assert "type:rule" in _row(eng, relabel["id"])["domain"].split(",")
    (stone,) = _tombstones(eng.root)
    assert set(stone) == {"id", "kind", "scope", "h1", "h2", "hv", "rejected_at", "via"}
    (receipt,) = _receipts(eng.root)
    assert "reject_reasons" not in receipt


def test_a_reject_without_reason_keeps_the_tombstone_text_free(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Skip code review for docs", "domain": "type:lesson"})

    _apply(tmp_path, capsys, [{"id": row["id"], "mark": "reject"}])

    (stone,) = _tombstones(eng.root)
    assert "reason" not in stone and "Skip code review" not in json.dumps(stone)


def test_reason_in_a_batch_row_never_reaches_the_tombstone(eng, tmp_path):
    row = eng.add_lesson({"summary": "Agents may not annotate rejections", "domain": "type:lesson"})

    batch_review_staging(eng, [{"id": row["id"], "action": "reject", "reason": "agent text"}],
                         dry_run=False, confirm=True)

    (stone,) = _tombstones(eng.root)
    assert "reason" not in stone


def test_expected_version_skips_an_item_edited_since(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Run the linters before pushing", "domain": "type:lesson"})
    seen = int(_row(eng, row["id"]).get("version") or 1)
    assert not eng.update_knowledge(row["id"], {"detail": "edited elsewhere"}).get("error")

    applied = _apply(tmp_path, capsys, [{"id": row["id"], "mark": "approve", "expected_version": seen}], code=1)

    assert applied["items"][0]["status"] == "version_conflict"
    assert _row(eng, row["id"])["tier"] == "staging"


def test_marks_reject_a_malformed_supersede_or_version():
    assert review_cli.validate_marks([{"id": "a1", "mark": "supersede:"}])[1]
    assert review_cli.validate_marks([{"id": "a1", "mark": "supersede:../x"}])[1]
    assert review_cli.validate_marks([{"id": "a1", "mark": "approve", "expected_version": "2"}])[1]
    assert review_cli.validate_marks([{"id": "a1", "mark": "approve", "expected_version": True}])[1]
    marks, error = review_cli.validate_marks([{"id": "a1", "mark": "Supersede:AbC-1", "expected_version": 3}])
    assert not error and marks == [{"id": "a1", "mark": "supersede", "target": "AbC-1", "expected_version": 3}]


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------


def test_receipt_names_the_route(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Keep one owner per service", "domain": "type:lesson"})

    _apply(tmp_path, capsys, [{"id": row["id"], "mark": "approve"}])

    (receipt,) = _receipts(eng.root)
    assert receipt["resource"] == "review/apply" and receipt["route"] == "marks"
    assert receipt["counts"]["applied"] == 1


def test_a_run_that_stops_part_way_still_leaves_a_receipt(eng, tmp_path, monkeypatch):
    first = eng.add_lesson({"summary": "First proposal to approve", "domain": "type:lesson"})
    second = eng.add_lesson({"summary": "Second proposal to approve", "domain": "type:lesson"})
    real = Engram.promote_knowledge
    calls = {"n": 0}

    def _promote(self, item_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk went away")
        return real(self, item_id, **kwargs)

    monkeypatch.setattr(Engram, "promote_knowledge", _promote)
    marks, _ = review_cli.validate_marks([{"id": first["id"], "mark": "approve"},
                                          {"id": second["id"], "mark": "approve"}])
    with pytest.raises(RuntimeError):
        review_cli.apply_marks(Engram(root=eng.root), marks, review_cli.attribution_record("owner", mode="marks"))

    (receipt,) = _receipts(eng.root)
    assert receipt["counts"]["aborted"] == 1
    assert receipt["counts"]["applied"] == 1


# ---------------------------------------------------------------------------
# one supersede target per run; an agent-proposed target is checked on approval
# ---------------------------------------------------------------------------


def _trusted_decision(eng: Engram, question: str, choice: str) -> dict:
    row = eng.add_decision({"question": question, "choice": choice})
    _approve(eng, row["id"])
    return _row(eng, row["id"])


def _revision(eng: Engram, question: str, choice: str, old_id: str) -> dict:
    """An agent's revision proposal: pending, carrying pending_supersedes."""
    row = eng.add_decision({"question": question, "choice": choice, "supersedes": old_id})
    stored = _row(eng, row["id"])
    assert stored["tier"] == "staging" and stored.get("pending_supersedes") == old_id, stored
    return stored


def _supersede_edges(eng: Engram) -> list[tuple[str, str]]:
    return [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]


def test_two_marks_superseding_one_entry_are_refused(eng, tmp_path, capsys):
    old = _trusted_decision(eng, "Where do build caches live?", "on each runner")
    owner_pick = eng.add_decision({"question": "Which store holds shared build caches?", "choice": "object store"})
    agent_pick = _revision(eng, "Where do build caches live now?", "in the shared bucket", old["id"])
    before = _knowledge(eng.root)
    marks = [{"id": owner_pick["id"], "mark": f"supersede:{old['id']}"}, {"id": agent_pick["id"], "mark": "approve"}]

    for extra in ([], ["--operator", "owner", "--yes"]):
        capsys.readouterr()
        assert review_cli.run_apply([str(_marks(tmp_path, marks)), *extra]) == 2
        assert old["id"] in capsys.readouterr().out

    assert _knowledge(eng.root) == before
    assert review_cli.validate_marks([{"id": "a1", "mark": "supersede:old001"},
                                      {"id": "b2", "mark": "supersede:old001"}])[1]


def test_a_target_decided_in_the_same_run_is_allowed():
    for raw in ([{"id": "a1", "mark": "approve"}, {"id": "b2", "mark": "supersede:a1"}],
                [{"id": "b2", "mark": "supersede:a1"}, {"id": "a1", "mark": "reject"}]):
        marks, error = review_cli.validate_marks(raw)
        assert not error and len(marks) == 2


# ---------------------------------------------------------------------------
# two phases: plain decisions first, then the ones that replace an entry
# ---------------------------------------------------------------------------


def _outcome(item: dict) -> tuple:
    """What a dry-run item and an applied item have in common."""
    status = item["status"]
    if status == "planned":
        status = "applied_unlinked" if item.get("unlinked_reason") else "applied"
    return (item["id"], item["action"], status, item.get("target", ""), item.get("unlinked_reason", ""),
            item.get("phase"), item.get("reason", ""))


def _dry_then_apply(eng, tmp_path, capsys, marks: list[dict], *, code: int = 0) -> tuple[dict, dict]:
    dry = _apply(tmp_path, capsys, marks, yes=False)
    applied = _apply(tmp_path, capsys, marks, code=code)
    assert [_outcome(i) for i in dry["items"]] == [_outcome(i) for i in applied["items"]]
    assert dry["order"] == applied["order"]
    return dry, applied


def test_approving_an_entry_and_its_agent_revision_in_one_run(eng, tmp_path, capsys):
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    revision = _revision(eng, "Where do build caches live now?", "in the shared bucket", first["id"])

    # the revision is listed first; it is still applied after the entry it replaces
    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [{"id": revision["id"], "mark": "approve"},
                                                            {"id": first["id"], "mark": "approve"}])

    assert [i["status"] for i in applied["items"]] == ["applied", "applied"]
    assert applied["items"][0]["target"] == first["id"]
    assert _supersede_edges(eng) == [(revision["id"], first["id"])]
    assert _row(eng, first["id"])["tier"] == _row(eng, revision["id"])["tier"] == "verified"


def test_approving_an_entry_and_superseding_it_in_one_run(eng, tmp_path, capsys):
    first = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    second = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [{"id": second["id"], "mark": f"supersede:{first['id']}"},
                                                            {"id": first["id"], "mark": "approve"}])

    assert [i["status"] for i in applied["items"]] == ["applied", "applied"]
    assert applied["counts"]["supersede"] == 1
    assert _supersede_edges(eng) == [(second["id"], first["id"])]


def test_rejecting_an_entry_fails_only_the_mark_that_supersedes_it(eng, tmp_path, capsys):
    first = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    second = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})
    third = eng.add_lesson({"summary": "Sign every release tag", "domain": "type:lesson"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": second["id"], "mark": f"supersede:{first['id']}"},
        {"id": first["id"], "mark": "reject"},
        {"id": third["id"], "mark": "approve"},
    ])

    assert [i["status"] for i in applied["items"]] == ["target_not_trusted", "applied", "applied"]
    assert _row(eng, second["id"])["tier"] == "staging"
    assert _row(eng, third["id"])["tier"] == "verified"
    assert _supersede_edges(eng) == []


def test_rejecting_an_entry_approves_its_agent_revision_without_the_link(eng, tmp_path, capsys):
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    revision = _revision(eng, "Where do build caches live now?", "in the shared bucket", first["id"])

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [{"id": revision["id"], "mark": "approve"},
                                                            {"id": first["id"], "mark": "reject"}])

    assert [i["status"] for i in applied["items"]] == ["applied_unlinked", "applied"]
    assert applied["items"][0]["unlinked_reason"] == "target_not_trusted"
    assert _supersede_edges(eng) == []


def test_approving_a_revision_whose_target_is_gone_approves_it_without_the_link(eng, tmp_path, capsys):
    old = _trusted_decision(eng, "Where do build caches live?", "on each runner")
    late = _revision(eng, "Where do build caches live now?", "in the shared bucket", old["id"])
    first = eng.add_decision({"question": "Which store holds shared build caches?", "choice": "object store"})
    _apply(tmp_path, capsys, [{"id": first["id"], "mark": f"supersede:{old['id']}"}])

    applied = _apply(tmp_path, capsys, [{"id": late["id"], "mark": "approve"}])

    (item,) = applied["items"]
    assert item["status"] == "applied_unlinked" and item["unlinked_reason"] == "target_not_trusted"
    assert applied["counts"]["approved_unlinked"] == 1
    assert _row(eng, late["id"])["tier"] == "verified"
    assert "pending_supersedes" not in _row(eng, late["id"])
    assert _supersede_edges(eng) == [(first["id"], old["id"])]


def test_approving_a_valid_revision_writes_its_link(eng, tmp_path, capsys):
    old = _trusted_decision(eng, "Where do build caches live?", "on each runner")
    revision = _revision(eng, "Where do build caches live now?", "in the shared bucket", old["id"])

    applied = _apply(tmp_path, capsys, [{"id": revision["id"], "mark": "approve"}])

    (item,) = applied["items"]
    assert item["status"] == "applied" and item["target"] == old["id"]
    assert _supersede_edges(eng) == [(revision["id"], old["id"])]


# ---------------------------------------------------------------------------
# a run that stops part-way
# ---------------------------------------------------------------------------


def test_a_stop_during_a_supersede_puts_the_pending_link_back(eng, tmp_path, monkeypatch):
    old = eng.add_lesson({"summary": "Store secrets in the CI settings page", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Store secrets in the CI secret store", "domain": "type:lesson"})

    def _boom(self, item_id, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(Engram, "promote_knowledge", _boom)
    marks, _ = review_cli.validate_marks([{"id": new["id"], "mark": f"supersede:{old['id']}"}])
    with pytest.raises(KeyboardInterrupt):
        review_cli.apply_marks(Engram(root=eng.root), marks, review_cli.attribution_record("owner", mode="marks"))

    row = _row(eng, new["id"])
    assert row["tier"] == "staging" and "pending_supersedes" not in row
    (receipt,) = _receipts(eng.root)
    assert receipt["counts"]["aborted"] == 1
    assert receipt["total_marks"] == 1 and receipt["aborted_at"] == new["id"]


# ---------------------------------------------------------------------------
# version guard: checked again right before each write; never on an agent path
# ---------------------------------------------------------------------------


def test_version_is_checked_again_right_before_the_write(eng, monkeypatch):
    first = eng.add_lesson({"summary": "First proposal in the batch", "domain": "type:lesson"})
    second = eng.add_lesson({"summary": "Second proposal in the batch", "domain": "type:lesson"})
    seen = int(_row(eng, second["id"]).get("version") or 1)
    real = Engram.promote_knowledge

    def _promote(self, item_id, **kwargs):
        result = real(self, item_id, **kwargs)
        if item_id == first["id"]:  # someone edits the second one meanwhile
            Engram(root=eng.root).update_knowledge(second["id"], {"detail": "edited meanwhile"})
        return result

    monkeypatch.setattr(Engram, "promote_knowledge", _promote)
    result = batch_review_staging(
        eng, [{"id": first["id"], "action": "approve"},
              {"id": second["id"], "action": "approve", "expected_version": seen}],
        dry_run=False, confirm=True, owner_cli=True,
    )

    assert [i["status"] for i in result["items"]] == ["applied", "version_conflict"]
    assert _row(eng, second["id"])["tier"] == "staging"


def test_agent_batches_ignore_expected_version_and_reason(tmp_path, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    root = tmp_path / "mcpstore"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    store = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", store)
    keep = store.add_lesson({"summary": "Agent batch approve", "domain": "t", "tier": "staging"})
    drop = store.add_lesson({"summary": "Agent batch reject", "domain": "t", "tier": "staging"})
    actions = [{"id": keep["id"], "action": "approve", "expected_version": 99},
               {"id": drop["id"], "action": "reject", "expected_version": 99, "reason": "AGENT NOTE"}]

    out = json.loads(asyncio.run(mcp_server.review_staging(
        action="batch", actions_json=json.dumps(actions), dry_run=False, confirm=True)))

    assert [i["status"] for i in out["items"]] == ["applied", "applied"]
    assert "AGENT NOTE" not in (root / "knowledge" / "tombstones.jsonl").read_text(encoding="utf-8")
    assert "AGENT NOTE" not in (root / "audit.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# exit codes; the export's marks help and template
# ---------------------------------------------------------------------------


def test_apply_returns_non_zero_only_when_every_mark_failed(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "A proposal that exists", "domain": "type:lesson"})
    args = ["--operator", "owner", "--yes"]

    assert review_cli.run_apply([str(_marks(tmp_path, [{"id": "nosuchid0001", "mark": "approve"}])), *args]) == 1
    both = [{"id": "nosuchid0002", "mark": "approve"}, {"id": row["id"], "mark": "approve"}]
    assert review_cli.run_apply([str(_marks(tmp_path, both)), *args]) == 0
    again = [{"id": row["id"], "mark": "approve"}]
    assert review_cli.run_apply([str(_marks(tmp_path, again)), *args]) == 0  # already done: not a failure
    capsys.readouterr()


def test_export_lists_every_mark_and_writes_a_template(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Pin the toolchain version", "domain": "type:lesson"})
    out = tmp_path / "export"

    assert review_cli.run_export(["--out", str(out)]) == 0

    text = (out / "review.md").read_text(encoding="utf-8")
    for token in ("approve", "reject", "edit-type:", "supersede:<id>", "skip", "reason", "expected_version"):
        assert token in text, token
    assert json.loads((out / "ids.json").read_text(encoding="utf-8")) == [row["id"]]
    template = json.loads((out / "marks-template.json").read_text(encoding="utf-8"))
    assert template == [{"id": row["id"], "kind": "lesson", "mark": "skip", "expected_version": 1}]
    marks, error = review_cli.validate_marks(template)
    assert not error and marks == [{"id": row["id"], "mark": "skip"}]
    before = _knowledge(eng.root)
    capsys.readouterr()
    assert review_cli.run_apply([str(out / "marks-template.json"), "--operator", "owner", "--yes"]) == 0
    assert _knowledge(eng.root) == before


# ---------------------------------------------------------------------------
# edit-type and retire / restore run after both review phases
# ---------------------------------------------------------------------------


def test_edit_type_and_supersede_of_one_proposal_with_template_versions(eng, tmp_path, capsys):
    old = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})
    review_cli.run_export(["--out", str(tmp_path / "export")])
    template = json.loads((tmp_path / "export" / "marks-template.json").read_text(encoding="utf-8"))
    version = next(e["expected_version"] for e in template if e["id"] == new["id"])

    # Relabeling the proposal to another type than the entry it replaces: the
    # type check uses the type the proposal has after this run (rule), so the
    # supersede is refused; the relabel itself still applies.
    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": new["id"], "mark": "edit-type:rule"},
        {"id": new["id"], "mark": f"supersede:{old['id']}", "expected_version": version},
    ])

    assert [(i["action"], i["status"], i["phase"]) for i in applied["items"]] == [
        ("edit-type", "applied", "edit"), ("supersede", "type_mismatch", 2)]
    assert applied["order"] == [new["id"], new["id"]]
    assert "type:rule" in _row(eng, new["id"])["domain"].split(",")
    assert (new["id"], old["id"]) not in _supersede_edges(eng)


def test_labeling_a_proposal_with_its_targets_type_and_superseding_in_one_run(eng, tmp_path, capsys):
    old = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "release"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": new["id"], "mark": "edit-type:lesson"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
    ])

    assert [(i["action"], i["status"]) for i in applied["items"]] == [("edit-type", "applied"),
                                                                       ("supersede", "applied")]
    assert (new["id"], old["id"]) in _supersede_edges(eng)


def test_edit_type_of_a_target_and_its_supersede(eng, tmp_path, capsys):
    # the target is relabeled to rule in the same run; a rule proposal may replace it
    old = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:rule"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": old["id"], "mark": "edit-type:rule"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
    ])

    assert [i["status"] for i in applied["items"]] == ["applied", "applied"]
    assert (new["id"], old["id"]) in _supersede_edges(eng)


def test_retiring_a_playbook_that_is_also_superseded(eng, tmp_path, capsys):
    old = eng.add_playbook({"title": "Rotate the signing key by hand",
                            "steps": [{"action": "Revoke the old key"}, {"action": "Mail the new key"}]})
    _approve(eng, old["id"])
    new = eng.add_playbook({"title": "Key rollover through the release tool",
                            "steps": [{"action": "Run the rotate command"}, {"action": "Publish the new key"}]})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": old["id"], "mark": "retire"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
    ])

    assert [(i["action"], i["status"], i["phase"]) for i in applied["items"]] == [
        ("retire", "already_applied", "lifecycle"), ("supersede", "applied", 2)]
    assert applied["order"] == [new["id"], old["id"]]
    assert eng._read_playbook_by_id(old["id"])["status"] != "active"


def test_restoring_a_playbook_that_is_also_superseded(eng, tmp_path, capsys):
    old = eng.add_playbook({"title": "Rotate the signing key by hand",
                            "steps": [{"action": "Revoke the old key"}, {"action": "Mail the new key"}]})
    _approve(eng, old["id"])
    new = eng.add_playbook({"title": "Key rollover through the release tool",
                            "steps": [{"action": "Run the rotate command"}, {"action": "Publish the new key"}]})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": old["id"], "mark": "restore"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
    ])

    assert [(i["action"], i["status"]) for i in applied["items"]] == [("restore", "applied"), ("supersede", "applied")]


# ---------------------------------------------------------------------------
# re-running a supersede; one review mark per id; chains in dependency order
# ---------------------------------------------------------------------------


def test_running_the_same_supersede_again_is_already_applied(eng, tmp_path, capsys):
    old = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})
    marks = [{"id": new["id"], "mark": f"supersede:{old['id']}"}]
    _apply(tmp_path, capsys, marks)

    dry, again = _dry_then_apply(eng, tmp_path, capsys, marks)

    assert again["items"][0]["status"] == "already_applied"
    assert again["counts"]["failed"] == 0 and again["counts"]["supersede_failed"] == 0
    receipt = _receipts(eng.root)[-1]
    assert receipt["counts"]["failed"] == 0
    assert _supersede_edges(eng) == [(new["id"], old["id"])]


def test_one_review_mark_per_id():
    for second in ("reject", "approve", "skip", "supersede:old001"):
        error = review_cli.validate_marks([{"id": "a1", "mark": "approve"}, {"id": "a1", "mark": second}])[1]
        assert "a1" in error, second
    marks, error = review_cli.validate_marks([{"id": "a1", "mark": "approve"}, {"id": "a1", "mark": "edit-type:rule"}])
    assert not error and len(marks) == 2


def test_a_chain_of_revisions_is_applied_oldest_first(eng, tmp_path, capsys):
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    middle = _revision(eng, "Where do build caches live now?", "in the shared bucket", first["id"])
    newest = _revision(eng, "Where do build caches live from now on?", "in the regional bucket", middle["id"])

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [{"id": newest["id"], "mark": "approve"},
                                                            {"id": middle["id"], "mark": "approve"},
                                                            {"id": first["id"], "mark": "approve"}])

    assert [i["status"] for i in applied["items"]] == ["applied", "applied", "applied"]
    assert applied["order"] == [first["id"], middle["id"], newest["id"]]
    assert sorted(_supersede_edges(eng)) == sorted([(middle["id"], first["id"]), (newest["id"], middle["id"])])


def test_an_owner_chain_is_applied_oldest_first(eng, tmp_path, capsys):
    oldest = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, oldest["id"])
    middle = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})
    newest = eng.add_lesson({"summary": "Tag and sign releases from the release workflow", "domain": "type:lesson"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": newest["id"], "mark": f"supersede:{middle['id']}"},
        {"id": middle["id"], "mark": f"supersede:{oldest['id']}"},
    ])

    assert [i["status"] for i in applied["items"]] == ["applied", "applied"]
    assert applied["order"] == [middle["id"], newest["id"]]


def test_a_stop_while_pointing_the_row_puts_it_back(eng, tmp_path, monkeypatch):
    old = eng.add_lesson({"summary": "Store secrets in the CI settings page", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Store secrets in the CI secret store", "domain": "type:lesson"})
    real = review_cli._set_pending_supersede
    calls = {"n": 0}

    def _stop_after_write(*args, **kwargs):
        calls["n"] += 1
        result = real(*args, **kwargs)
        if calls["n"] == 1:
            raise KeyboardInterrupt  # the write landed, the caller never saw it
        return result

    monkeypatch.setattr(review_cli, "_set_pending_supersede", _stop_after_write)
    marks, _ = review_cli.validate_marks([{"id": new["id"], "mark": f"supersede:{old['id']}"}])
    with pytest.raises(KeyboardInterrupt):
        review_cli.apply_marks(Engram(root=eng.root), marks, review_cli.attribution_record("owner", mode="marks"))

    row = _row(eng, new["id"])
    assert row["tier"] == "staging" and "pending_supersedes" not in row



def test_relabeling_a_target_to_another_type_refuses_its_supersede(eng, tmp_path, capsys):
    old = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, old["id"])
    new = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": old["id"], "mark": "edit-type:rule"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
    ])

    assert [i["status"] for i in applied["items"]] == ["applied", "type_mismatch"]


# ---------------------------------------------------------------------------
# edit-type on archived playbooks; decisions relabel through the Owner's path
# ---------------------------------------------------------------------------


def _playbooks(eng):
    old = eng.add_playbook({"title": "Rotate the signing key by hand",
                            "steps": [{"action": "Revoke the old key"}, {"action": "Mail the new key"}]})
    _approve(eng, old["id"])
    new = eng.add_playbook({"title": "Key rollover through the release tool",
                            "steps": [{"action": "Run the rotate command"}, {"action": "Publish the new key"}]})
    return old, new


def test_edit_type_of_a_playbook_its_replacement_archives_is_skipped(eng, tmp_path, capsys):
    old, new = _playbooks(eng)
    marks = [{"id": old["id"], "mark": "edit-type:rule"}, {"id": new["id"], "mark": f"supersede:{old['id']}"}]

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, marks)

    assert [(i["action"], i["status"], i.get("reason")) for i in applied["items"]] == [
        ("edit-type", "skipped", "archived"), ("supersede", "applied", None)]
    assert applied["counts"]["failed"] == 0 and applied["counts"]["edit_type_failed"] == 0
    _dry, again = _dry_then_apply(eng, tmp_path, capsys, marks)  # exits 0 again
    assert [i["status"] for i in again["items"]] == ["skipped", "already_applied"]


def test_edit_type_of_an_archived_playbook_is_skipped(eng, tmp_path, capsys):
    old, _new = _playbooks(eng)
    eng.archive_playbook(old["id"])
    marks = [{"id": old["id"], "mark": "edit-type:rule"}]

    for _ in range(2):
        _dry, applied = _dry_then_apply(eng, tmp_path, capsys, marks)
        assert [(i["status"], i["reason"]) for i in applied["items"]] == [("skipped", "archived")]


def test_edit_type_of_a_decision_is_written_and_checked(eng, tmp_path, capsys):
    row = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk", "domain": "infra"})
    _approve(eng, row["id"])
    marks = [{"id": row["id"], "mark": "edit-type:rule"}]

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, marks)
    assert applied["items"][0]["status"] == "applied"
    assert set(_row(eng, row["id"])["domain"].split(",")) == {"infra", "type:rule"}

    _dry, again = _dry_then_apply(eng, tmp_path, capsys, marks)
    assert again["items"][0]["status"] == "already_applied"


def test_an_edit_type_that_does_not_land_is_reported_failed(eng, tmp_path, capsys, monkeypatch):
    row = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk", "domain": "infra"})
    _approve(eng, row["id"])
    monkeypatch.setattr(review_cli, "relabel_type", lambda *args, **kwargs: None)  # writes nothing

    applied = _apply(tmp_path, capsys, [{"id": row["id"], "mark": "edit-type:rule"}], code=1)

    assert applied["items"][0]["status"] == "failed"


def test_agents_still_cannot_change_a_decisions_domain(eng, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    row = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk", "domain": "infra"})
    _approve(eng, row["id"])

    eng.update_decision(row["id"], {"domain": "type:rule"})
    assert _row(eng, row["id"])["domain"] == "infra"
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    monkeypatch.setattr(mcp_server, "_engram", Engram(root=eng.root))
    asyncio.run(mcp_server.update_knowledge(row["id"], json.dumps({"domain": "type:rule"})))
    assert _row(eng, row["id"])["domain"] == "infra"


# ---------------------------------------------------------------------------
# phase-2 order: linear in lookups; loops keep file order and write no loop
# ---------------------------------------------------------------------------


class _CountingStore:
    """Just enough of a store for planning: rows that carry an agent's pending_supersedes."""

    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.lookups = 0

    def _find_item_by_id(self, item_id):
        self.lookups += 1
        row = self.rows.get(item_id)
        return ("decision", row) if row is not None else (None, None)


def test_a_long_reversed_chain_is_ordered_with_one_lookup_per_mark():
    n = 200
    ids = [f"rev{i:04d}" for i in range(n)]
    rows = {ids[0]: {"id": ids[0], "tier": "staging"}}
    for i in range(1, n):
        rows[ids[i]] = {"id": ids[i], "tier": "staging", "pending_supersedes": ids[i - 1]}
    store = _CountingStore(rows)
    marks = [{"id": item_id, "mark": "approve"} for item_id in reversed(ids)]

    plan = review_cli._plan(store, marks)

    assert [m["id"] for _n, m, _phase in plan] == ids
    assert store.lookups == n


@pytest.mark.parametrize("size", [2, 3])
def test_a_supersede_loop_keeps_file_order_and_writes_no_loop(eng, tmp_path, capsys, size):
    rows = [eng.add_lesson({"summary": f"Loop member {i} of {size}", "domain": "type:lesson"})["id"]
            for i in range(size)]
    marks = [{"id": rows[i], "mark": f"supersede:{rows[(i + 1) % size]}"} for i in range(size)]

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, marks, code=1)

    assert applied["order"] == rows
    assert _supersede_edges(eng) == []


# ---------------------------------------------------------------------------
# type checks skip playbooks that are retired; relabels leave a trail
# ---------------------------------------------------------------------------


def _typed_playbook(eng, title: str, mem_type: str, *, approve: bool = False) -> dict:
    row = eng.add_playbook({"title": title, "domain": f"type:{mem_type}",
                            "steps": [{"action": "Run the first step"}, {"action": "Run the second step"}]})
    if approve:
        _approve(eng, row["id"])
    return row


def test_a_playbook_its_replacement_archives_keeps_its_label_in_the_type_check(eng, tmp_path, capsys):
    old = _typed_playbook(eng, "Rotate the signing key by hand", "rule", approve=True)
    new = _typed_playbook(eng, "Key rollover through the release tool", "lesson")
    replace = {"id": new["id"], "mark": f"supersede:{old['id']}"}

    alone = _apply(tmp_path, capsys, [replace], yes=False)
    assert alone["items"][0]["status"] == "type_mismatch"

    dry, applied = _dry_then_apply(eng, tmp_path, capsys,
                                   [{"id": old["id"], "mark": "edit-type:lesson"}, replace])

    for payload in (dry, applied):
        assert [i["status"] for i in payload["items"] if i["action"] == "supersede"] == ["type_mismatch"]
    assert (new["id"], old["id"]) not in _supersede_edges(eng)
    assert eng._read_playbook_by_id(old["id"])["status"] == "active"


def test_final_types_leave_out_playbooks_that_keep_their_label(eng):
    old = _typed_playbook(eng, "Rotate the signing key by hand", "rule", approve=True)
    new = _typed_playbook(eng, "Key rollover through the release tool", "lesson")
    gone = _typed_playbook(eng, "Mail keys on paper", "rule", approve=True)
    eng.archive_playbook(gone["id"])
    lesson = eng.add_lesson({"summary": "Tag releases by hand", "domain": "type:lesson"})
    _approve(eng, lesson["id"])
    newer = eng.add_lesson({"summary": "Tag releases from the release workflow", "domain": "type:lesson"})
    marks, error = review_cli.validate_marks([
        {"id": old["id"], "mark": "edit-type:lesson"},
        {"id": gone["id"], "mark": "edit-type:lesson"},
        {"id": lesson["id"], "mark": "edit-type:rule"},
        {"id": new["id"], "mark": f"supersede:{old['id']}"},
        {"id": newer["id"], "mark": f"supersede:{lesson['id']}"},
    ])
    assert not error

    assert review_cli._final_types(eng, marks) == {lesson["id"]: "rule"}
    assert review_cli._final_types(eng, [m for m in marks if m["id"] != new["id"]]) == {
        old["id"]: "lesson", lesson["id"]: "rule"}


def test_edit_type_items_say_which_label_they_change_from_and_to(eng, tmp_path, capsys):
    decision = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk",
                                 "domain": "infra,type:lesson"})
    _approve(eng, decision["id"])
    bare = eng.add_lesson({"summary": "Pin the runner image", "domain": "ci"})
    _approve(eng, bare["id"])
    playbook = _typed_playbook(eng, "Rotate the signing key by hand", "rule", approve=True)
    marks = [{"id": decision["id"], "mark": "edit-type:rule"},
             {"id": bare["id"], "mark": "edit-type:lesson"},
             {"id": playbook["id"], "mark": "edit-type:lesson"}]

    dry, applied = _dry_then_apply(eng, tmp_path, capsys, marks)

    expected = [("lesson", "rule"), (None, "lesson"), ("rule", "lesson")]
    for payload in (dry, applied):
        assert [(i["from"], i["to"]) for i in payload["items"]] == expected
    assert [i["status"] for i in applied["items"]] == ["applied"] * 3

    _dry, again = _dry_then_apply(eng, tmp_path, capsys, marks)
    assert [i["status"] for i in again["items"]] == ["already_applied"] * 3
    assert [(i["from"], i["to"]) for i in again["items"]] == [("rule", "rule"), ("lesson", "lesson"),
                                                              ("lesson", "lesson")]


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
@pytest.mark.parametrize("bad", ["", "bogus", "rule,project_fact", "rule\nlesson", "type:rule", "Rule"])
def test_relabel_type_refuses_a_type_that_is_not_one_of_the_five(eng, kind, bad):
    if kind == "lesson":
        row = eng.add_lesson({"summary": "Pin the runner image", "domain": "ci"})
    elif kind == "decision":
        row = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk", "domain": "ci"})
    else:
        row = _typed_playbook(eng, "Rotate the signing key by hand", "rule")
    _approve(eng, row["id"])
    before = _knowledge(eng.root)

    assert review_cli.relabel_type(eng, kind, row["id"], bad) == {"error": "invalid_type"}

    assert _knowledge(eng.root) == before


def test_independent_replacements_keep_file_order_and_a_chain_goes_oldest_first(eng, tmp_path, capsys):
    def _lesson(summary: str, *, approved: bool = False) -> str:
        row = eng.add_lesson({"summary": summary, "domain": "type:lesson"})
        if approved:
            _approve(eng, row["id"])
        return row["id"]

    old1, old2, old3 = (_lesson(f"Older practice {i}", approved=True) for i in (1, 2, 3))
    c0 = _lesson("Chain root", approved=True)
    new1, new2, new3 = (_lesson(f"Newer practice {i}") for i in (1, 2, 3))
    c1, c2 = _lesson("Chain middle"), _lesson("Chain newest")

    _dry, applied = _dry_then_apply(eng, tmp_path, capsys, [
        {"id": new1, "mark": f"supersede:{old1}"},
        {"id": c2, "mark": f"supersede:{c1}"},
        {"id": new2, "mark": f"supersede:{old2}"},
        {"id": c1, "mark": f"supersede:{c0}"},
        {"id": new3, "mark": f"supersede:{old3}"},
    ])

    assert [i["status"] for i in applied["items"]] == ["applied"] * 5
    order = applied["order"]
    assert [i for i in order if i in (new1, new2, new3)] == [new1, new2, new3]
    assert order.index(c1) < order.index(c2)
    assert order == [new1, new2, c1, c2, new3]
