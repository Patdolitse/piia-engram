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
    assert applied["items"] == [{"id": new["id"], "action": "supersede", "status": "applied", "target": old["id"]}]
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
    new = eng.add_lesson({"summary": "Cache the dependency layer between CI runs", "domain": "type:lesson"})
    rule = eng.add_lesson({"summary": "Never cache build output", "domain": "type:rule"})
    _approve(eng, rule["id"])
    before = _knowledge(eng.root)

    applied = _apply(tmp_path, capsys, [
        {"id": new["id"], "mark": "supersede:nosuchid0001"},
        {"id": new["id"], "mark": f"supersede:{pending['id']}"},
        {"id": new["id"], "mark": f"supersede:{new['id']}"},
        {"id": new["id"], "mark": f"supersede:{rule['id']}"},
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
    assert payload["items"] == [{"id": row["id"], "action": "supersede", "status": "self", "target": row["id"]}]
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
    assert applied["items"] == [{"id": keep["id"], "action": "approve", "status": "applied"},
                                {"id": drop["id"], "action": "reject", "status": "applied"}]
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


def test_a_target_decided_in_the_same_run_is_refused():
    error = review_cli.validate_marks([{"id": "a1", "mark": "approve"}, {"id": "b2", "mark": "supersede:a1"}])[1]
    assert "a1" in error
    assert review_cli.validate_marks([{"id": "b2", "mark": "supersede:a1"}, {"id": "a1", "mark": "reject"}])[1]


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
