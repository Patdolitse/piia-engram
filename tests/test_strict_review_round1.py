"""4.21.1 independent review, round 1: B1-B3 and N1-N7 regressions."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from piia_engram import strict_mode
from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(json.dumps({"role": "developer"}), encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    import piia_engram.mcp_server as m

    m._engram = Engram(root)
    return m, root


def _audit(root: Path) -> list[dict]:
    path = root / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reject(eng, item_id):
    assert batch_review_staging(eng, [{"id": item_id, "action": "reject"}], dry_run=False, confirm=True)[
        "counts"]["applied"] == 1


# ---------------------------------------------------------------------------
# B1: a refused insert is reported to the agent as refused
# ---------------------------------------------------------------------------


def _rejected_lesson(m, text):
    row = m._engram.add_lesson(text, domain="t", tier="staging")
    _reject(m._engram, row["id"])
    return row


def test_mcp_add_lesson_reports_rejected_before(env):
    m, root = env
    row = _rejected_lesson(m, "never commit on friday")

    out = _run(m.add_lesson(summary="never commit on friday", user_confirmed=True))

    assert "已记录" not in out and "recorded" not in out.lower()
    data = json.loads(out)
    assert data["status"] == "rejected_before" and data["rejection_id"] == row["id"]


def test_mcp_add_decision_reports_rejected_before(env):
    m, root = env
    row = m._engram.add_decision({"question": "ship on friday?", "choice": "no", "reasoning": "r",
                                   "tier": "staging"})
    _reject(m._engram, row["id"])

    out = _run(m.add_decision(question="ship on friday?", choice="no", user_confirmed=True))

    assert json.loads(out)["status"] == "rejected_before"


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
def test_mcp_memory_store_reports_refusals_for_every_kind(env, monkeypatch, kind):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    content = {
        "lesson": {"summary": "a lesson the owner rejected"},
        "decision": {"question": "a decision the owner rejected?", "choice": "no"},
        "playbook": {"title": "A procedure the owner rejected", "triggers": "x",
                     "steps_json": json.dumps(["one", "two", "three"])},
    }[kind]
    first = _run(m.memory_store(kind=kind, content_json=json.dumps(content), user_confirmed=True))
    rows = {"lesson": m._engram.get_lessons, "decision": m._engram.get_decisions}
    if kind == "playbook":
        pid = json.loads(first).get("id") if first.startswith("{") else None
        pid = pid or [p for p in (root / "playbooks").glob("*.json") if not p.name.startswith("_")][0].stem
    else:
        pid = rows[kind](limit=None, _update_access=False)[0]["id"]
    _reject(m._engram, pid)

    out = _run(m.memory_store(kind=kind, content_json=json.dumps(content), user_confirmed=True))

    assert "已记录" not in out
    assert json.loads(out)["status"] == "rejected_before"


def test_mcp_add_lesson_reports_duplicate_retired(env):
    m, root = env
    row = m._engram.add_lesson("a lesson that was retired", domain="t")
    m._engram.archive_knowledge(row["id"])

    out = json.loads(_run(m.add_lesson(summary="a lesson that was retired", user_confirmed=True)))

    assert out["status"] == "duplicate_retired" and out["existing_id"] == row["id"]


def test_bulk_add_counts_refusals_as_not_saved(env):
    m, root = env
    _rejected_lesson(m, "bulk rejected lesson")

    result = m._engram.bulk_add_lessons([{"summary": "bulk rejected lesson"}, {"summary": "bulk fresh lesson"}])

    assert result["saved"] == 1
    assert any(r.get("status") == "rejected_before" for r in result["results"])


# ---------------------------------------------------------------------------
# B2: check_anchors is an Owner action under strict
# ---------------------------------------------------------------------------


def test_strict_check_anchors_is_refused_and_changes_nothing(env, monkeypatch):
    m, root = env
    m._engram.add_lesson("anchored verified lesson", domain="t")
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    before = (root / "knowledge" / "lessons.json").read_bytes()

    out = _run(m.check_anchors(project_root=str(root.parent)))

    assert m._gov_rt.is_governance_refusal(out) and "ENGRAM_APPROVAL=strict" in out
    assert (root / "knowledge" / "lessons.json").read_bytes() == before


# ---------------------------------------------------------------------------
# B3: the latch is looked up on the store actually in use
# ---------------------------------------------------------------------------


def test_latched_store_is_strict_even_when_engram_dir_points_elsewhere(env, monkeypatch, tmp_path):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    monkeypatch.delenv("ENGRAM_APPROVAL")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(elsewhere))

    out = _run(m.update_identity(field="profile", updates_json='{"role": "x"}'))

    assert "ENGRAM_APPROVAL=strict" in out
    assert "wrap_up_session" not in m.server_instructions()
    actions = json.dumps([{"id": "x", "action": "approve"}])
    assert "ENGRAM_APPROVAL=strict" in _run(
        m.review_staging(action="batch", actions_json=actions, dry_run=False, confirm=True))


def test_store_root_resolution_matches_engram(monkeypatch, tmp_path):
    from piia_engram.storage import _engram_root

    monkeypatch.setenv("ENGRAM_DIR", "~/some-engram-dir")

    assert strict_mode._store_root() == _engram_root()
    assert "~" not in str(strict_mode._store_root())


# ---------------------------------------------------------------------------
# N1: wrap_up under strict writes no project snapshot
# ---------------------------------------------------------------------------


def test_strict_wrap_up_skips_the_project_snapshot(env, monkeypatch, tmp_path):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    project = tmp_path / "proj"
    project.mkdir()

    out = json.loads(_run(m.wrap_up_session(summary="short session", project_folder=str(project),
                                            user_confirmed=True)))

    assert out["project_snapshot"]["saved"] is False
    assert not any((root / "projects").glob("*.json")) if (root / "projects").exists() else True


# ---------------------------------------------------------------------------
# N2: clearing the latch leaves a receipt first, and never with audit off
# ---------------------------------------------------------------------------


def _cli(monkeypatch, capsys, *argv):
    from piia_engram import setup_wizard

    monkeypatch.setattr(sys, "argv", ["engram", "review", *argv])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    return int(exc.value.code or 0), capsys.readouterr().out


def test_clear_is_refused_when_audit_is_off(env, monkeypatch, capsys):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    monkeypatch.delenv("ENGRAM_APPROVAL")
    monkeypatch.setenv("ENGRAM_AUDIT", "0")

    code, _ = _cli(monkeypatch, capsys, "strict-marker", "--clear", "--operator", "owner", "--yes")

    assert code != 0 and (root / "approval_mode.json").exists()


def test_clear_receipt_is_written_even_if_the_delete_fails(env, monkeypatch, capsys):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    monkeypatch.delenv("ENGRAM_APPROVAL")

    def boom(_root):
        raise OSError("delete failed")

    monkeypatch.setattr(strict_mode, "clear_marker", boom)
    with pytest.raises(OSError):
        _cli(monkeypatch, capsys, "strict-marker", "--clear", "--operator", "owner", "--yes")

    assert any(a.get("verb") == "strict-marker-clear" for a in _audit(root))


# ---------------------------------------------------------------------------
# N3: doctor sees an unfinished playbook reject
# ---------------------------------------------------------------------------


def test_unfinished_playbook_reject_is_listed(env, monkeypatch):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    pb = m._engram.add_playbook({"title": "Half rejected procedure", "steps": ["a", "b", "c"]})
    from piia_engram import tombstones

    tombstones.append(root, "playbook", pb, via="cli:owner")  # the archive never landed

    assert {"id": pb["id"], "kind": "playbook", "tier": "staging"} in m._engram.tombstoned_but_pending()


# ---------------------------------------------------------------------------
# N5: a version snapshot never blocks a new row as duplicate_retired
# ---------------------------------------------------------------------------


def test_version_snapshots_do_not_count_as_retired(env):
    m, root = env
    path = root / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows.append({"id": "snap00000001", "summary": "an older version of a live lesson", "status": "superseded",
                 "snapshot_of": "live00000001", "tier": "verified"})
    from knowledge_seed import raw_write_json

    raw_write_json(path, rows)

    result = m._engram.add_lesson("an older version of a live lesson", domain="t")

    assert result.get("status") != "duplicate_retired"


# ---------------------------------------------------------------------------
# N6: strict instructions ask for a type label and a reason
# ---------------------------------------------------------------------------


def test_strict_instructions_ask_for_type_and_reason(env, monkeypatch):
    m, root = env
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    text = m.server_instructions()

    assert "type:rule" in text and "why" in text


# ---------------------------------------------------------------------------
# N7: the Owner can withdraw a rejection
# ---------------------------------------------------------------------------


def test_untombstone_needs_an_operator_and_lets_the_claim_back(env, monkeypatch, capsys):
    m, root = env
    row = _rejected_lesson(m, "a rejection the owner withdraws")

    code, out = _cli(monkeypatch, capsys, "untombstone", row["id"])
    assert code == 0 and "dry_run" in out
    code, _ = _cli(monkeypatch, capsys, "untombstone", row["id"], "--yes")
    assert code != 0
    code, _ = _cli(monkeypatch, capsys, "untombstone", row["id"], "--operator", "owner", "--yes")
    assert code == 0

    again = m._engram.add_lesson("a rejection the owner withdraws, reworded", domain="t")
    assert again.get("status") != "rejected_before"
    assert any(a.get("verb") == "untombstone" for a in _audit(root))


# ---------------------------------------------------------------------------
# Review round 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["strict", None])
def test_session_draft_never_merges_into_a_retired_playbook(env, monkeypatch, mode):
    m, root = env
    retired = m._engram.add_playbook({"title": "Release steps", "steps": ["build", "test", "upload"],
                                      "tier": "staging"})
    m._engram.archive_playbook(retired["id"])
    path = root / "playbooks" / f"{retired['id']}.json"
    before = path.read_bytes()
    if mode:
        monkeypatch.setenv("ENGRAM_APPROVAL", mode)
    real = Engram.add_playbook

    def retired_twin(self, playbook, *args, **kwargs):
        return {"status": "duplicate_retired", "existing_id": retired["id"], "where": "retired"}

    monkeypatch.setattr(Engram, "add_playbook", retired_twin)
    result = m._engram.extract_playbook_from_session(
        "Steps: 1. first build the package, 2. then run the tests, 3. then upload the wheel."
    )
    monkeypatch.setattr(Engram, "add_playbook", real)

    assert result is None
    assert path.read_bytes() == before
    assert not [p for p in (root / "playbooks").glob("*-prev-*")]


def test_merge_and_content_update_refuse_a_non_active_playbook(env):
    m, root = env
    pb = m._engram.add_playbook({"title": "Old procedure", "steps": ["a", "b", "c"]})
    m._engram.archive_playbook(pb["id"])
    path = root / "playbooks" / f"{pb['id']}.json"
    before = path.read_bytes()

    assert m._engram.merge_playbooks(pb["id"], {"title": "x", "steps": ["d"]}).get("error") == "not_active"
    assert m._engram.update_playbook(pb["id"], {"description": "edit"}).get("error") == "not_active"
    assert path.read_bytes() == before
    restored = m._engram.restore_playbook(pb["id"], dry_run=False, confirm=True)
    assert not restored.get("error")


def test_concurrent_tombstone_appends_are_all_kept(env):
    import threading

    from piia_engram import tombstones

    m, root = env
    rows = [{"id": f"id{i:010d}", "summary": f"claim number {i}"} for i in range(40)]
    threads = [threading.Thread(target=tombstones.append, args=(root, "lesson", row), kwargs={"via": "t"})
               for row in rows]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert {s["id"] for s in tombstones.load(root)} == {r["id"] for r in rows}


def test_untombstone_is_refused_with_audit_off_and_receipted_first(env, monkeypatch, capsys):
    m, root = env
    row = _rejected_lesson(m, "a rejection kept while audit is off")
    monkeypatch.setenv("ENGRAM_AUDIT", "0")
    code, _ = _cli(monkeypatch, capsys, "untombstone", row["id"], "--operator", "owner", "--yes")
    assert code != 0
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    from piia_engram import tombstones

    def boom(_root, _id):
        raise OSError("remove failed")

    monkeypatch.setattr(tombstones, "remove", boom)
    with pytest.raises(OSError):
        _cli(monkeypatch, capsys, "untombstone", row["id"], "--operator", "owner", "--yes")
    assert any(a.get("verb") == "untombstone" for a in _audit(root))


@pytest.mark.parametrize("framed", ["Lesson: never commit on friday", "教训：never commit on friday",
                                    "  NOTE : Never commit on Friday.", "**Lesson:** never commit on friday",
                                    "__决策__：never commit on friday"])
def test_a_label_prefix_does_not_get_around_a_tombstone(env, framed):
    m, root = env
    row = _rejected_lesson(m, "never commit on friday")

    again = m._engram.add_lesson(framed, domain="t")

    assert again.get("status") == "rejected_before" and again.get("rejection_id") == row["id"]


def test_tombstones_carry_a_hash_version_and_other_versions_match_nothing(env, monkeypatch, capsys):
    from piia_engram import tombstones

    m, root = env
    row = _rejected_lesson(m, "a claim rejected under the current hashing")
    (stone,) = tombstones.load(root)
    assert stone["hv"] == tombstones.HASH_VERSION

    old = dict(stone, id="old000000001", hv=1)
    path = root / "knowledge" / "tombstones.jsonl"
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")  # only a stale-version record left

    assert m._engram.add_lesson("a claim rejected under the current hashing", domain="t").get("status") \
        != "rejected_before"
    assert tombstones.stale_version_ids(root) == ["old000000001"]
    from piia_engram import setup_wizard  # noqa: F401
    from piia_engram.doctor import _run_functional_checks

    _run_functional_checks()
    assert "older hash version" in capsys.readouterr().out
    assert row["id"]
