"""``engram review apply`` marks: supersede, reject reasons, version guards, receipts.

* ``supersede:<old id>`` approves a pending proposal and records that it
  replaces an approved entry (lesson / decision: a ``supersedes`` edge, so
  recall shows only the new one; playbook: the old one is archived);
* the target must exist, be trusted, be the same kind and scope, not be the
  proposal and not close a cycle; otherwise the item fails and nothing is written;
* a reject mark may carry the Owner's ``reason``, cleaned and capped onto the tombstone;
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


def _apply(tmp_path: Path, capsys, marks: list[dict], *, yes: bool = True) -> dict:
    capsys.readouterr()
    args = [str(_marks(tmp_path, marks))] + (["--operator", "owner", "--yes"] if yes else [])
    assert review_cli.run_apply(args) == 0
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
    ])

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


def test_reject_reason_is_cleaned_onto_the_tombstone(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Skip the changelog for small fixes", "domain": "type:lesson"})

    _apply(tmp_path, capsys, [{"id": row["id"], "mark": "reject", "reason": "no\x1b[2J: every fix\nis logged"}])

    (stone,) = _tombstones(eng.root)
    assert stone["reason"] == "no [2J: every fix is logged"
    assert stone["via"] == "cli:owner"


def test_a_reject_without_reason_keeps_the_tombstone_text_free(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Skip code review for docs", "domain": "type:lesson"})

    _apply(tmp_path, capsys, [{"id": row["id"], "mark": "reject"}])

    (stone,) = _tombstones(eng.root)
    assert "reason" not in stone and "Skip code review" not in json.dumps(stone)


def test_reason_from_an_agent_batch_is_ignored(eng, tmp_path):
    row = eng.add_lesson({"summary": "Agents may not annotate rejections", "domain": "type:lesson"})

    batch_review_staging(eng, [{"id": row["id"], "action": "reject", "reason": "agent text"}],
                         dry_run=False, confirm=True)

    (stone,) = _tombstones(eng.root)
    assert "reason" not in stone


def test_expected_version_skips_an_item_edited_since(eng, tmp_path, capsys):
    row = eng.add_lesson({"summary": "Run the linters before pushing", "domain": "type:lesson"})
    seen = int(_row(eng, row["id"]).get("version") or 1)
    assert not eng.update_knowledge(row["id"], {"detail": "edited elsewhere"}).get("error")

    applied = _apply(tmp_path, capsys, [{"id": row["id"], "mark": "approve", "expected_version": seen}])

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
