"""Local owner review window: displayed-snapshot binding and zero-write reads."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram import review_cli, tombstones, write_provenance


def ui():
    return importlib.import_module("piia_engram.review_ui")


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    return Engram(root=tmp_path / "store")


def proposed(eng, kind):
    if kind == "decision":
        return eng.add_decision({"question": "Synthetic selection", "choice": "Local tool",
                                 "reasoning": "Owner can review", "domain": "type:decision"})
    if kind == "playbook":
        return eng.add_playbook({"title": "Synthetic procedure", "description": "Full procedure",
                                "steps": [{"action": "first step"}, {"action": "last step"}],
                                "domain": "type:lesson"})
    if kind == "identity":
        return eng.propose_identity("profile", {"role": "synthetic new role"})
    return eng.add_lesson({"summary": "Synthetic proposal", "detail": "理由与依据\n复查时间：2027-01-10",
                           "domain": "type:" + kind})


def card(eng, row):
    return next(c for c in ui().ReviewController(eng.root).pending() if c.item_id == row["id"])


@pytest.mark.parametrize("kind", ["rule", "preference", "project_fact", "lesson", "decision", "playbook", "identity"])
def test_queue_and_full_details_are_zero_write(eng, kind):
    row = proposed(eng, kind)
    before = snapshot(eng.root)
    current = card(eng, row)
    assert current.version >= 1 and current.fingerprint
    assert "Synthetic" in current.text or "synthetic new role" in current.text
    if kind == "playbook":
        assert "first step" in current.text and "last step" in current.text
    if kind == "identity":
        assert "old" in current.text and "new" in current.text
    assert snapshot(eng.root) == before


@pytest.mark.parametrize("kind", ["rule", "preference", "project_fact", "lesson", "decision", "playbook", "identity"])
def test_local_click_reuses_apply_marks_and_records_owner_ui(eng, kind, monkeypatch):
    row = proposed(eng, kind)
    controller = ui().ReviewController(eng.root)
    current = card(eng, row)
    calls = []
    apply = review_cli.apply_marks

    def tracked(store, marks, attribution, **kwargs):
        calls.append((marks, attribution))
        return apply(store, marks, attribution, **kwargs)

    monkeypatch.setattr(review_cli, "apply_marks", tracked)
    result = controller.decide(current, "approve")
    assert result["ok"] and result["changed"], result
    assert len(calls) == 1
    assert calls[0][0] == [{"id": row["id"], "mark": "approve", "expected_version": current.version}]
    assert calls[0][1]["route"] == "owner_ui" and calls[0][1]["operator"] == "owner"
    assert row["id"] not in [c.item_id for c in controller.pending()]
    if kind == "identity":
        assert Engram(root=eng.root, read_only=True).get_profile()["role"] == "synthetic new role"
    else:
        assert Engram(root=eng.root, read_only=True)._find_item_by_id(row["id"])[1]["tier"] == "verified"
    audit = [json.loads(line) for line in (eng.root / "audit.log").read_text(encoding="utf-8").splitlines()]
    receipts = [r for r in audit if r.get("action") == "owner_ui"]
    assert receipts and receipts[-1]["source_tool"] == "owner_ui"


def test_reject_uses_text_free_tombstone(eng):
    row = proposed(eng, "lesson")
    result = ui().ReviewController(eng.root).decide(card(eng, row), "reject")
    assert result["ok"] and result["changed"]
    assert "Synthetic proposal" not in json.dumps(tombstones.load(eng.root))
    assert row["id"] not in [c.item_id for c in ui().ReviewController(eng.root).pending()]


@pytest.mark.parametrize("action", ["later", "cancel", "", "trust", "archive"])
def test_non_review_actions_never_write(eng, action):
    row = proposed(eng, "lesson")
    current = card(eng, row)
    before = snapshot(eng.root)
    assert not ui().ReviewController(eng.root).decide(current, action)["changed"]
    assert snapshot(eng.root) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_mcp_origin_refuses_before_any_write(eng, action):
    row = proposed(eng, "lesson")
    current = card(eng, row)
    before = snapshot(eng.root)
    with write_provenance.origin_scope("mcp", client_name="owner"):
        result = ui().ReviewController(eng.root).decide(current, action)
    assert result["status"] == "local_review_only" and not result["changed"]
    assert snapshot(eng.root) == before


def test_changed_content_even_without_version_bump_is_not_approved(eng):
    row = proposed(eng, "lesson")
    current = card(eng, row)
    def change(item):
        item["detail"] = "Changed since display"
        return item
    eng._update_knowledge_item("lesson", row["id"], change)
    assert review_cli._row_version(eng._find_item_by_id(row["id"])[1]) == current.version
    result = ui().ReviewController(eng.root).decide(current, "approve")
    assert not result["ok"] and result["status"] == "version_conflict"
    assert eng._find_item_by_id(row["id"])[1]["tier"] == "staging"


def test_replacement_preview_includes_target_and_detects_target_edit(eng):
    old = proposed(eng, "lesson")
    review_cli.apply_marks(eng, [{"id": old["id"], "mark": "approve"}], {"operator": "owner"})
    new = eng.add_lesson({"summary": "New synthetic proposal", "detail": "New content",
                          "domain": "type:lesson", "pending_supersedes": old["id"]})
    eng._update_knowledge_item("lesson", new["id"], lambda row: {**row, "pending_supersedes": old["id"]})
    current = card(eng, new)
    assert "Synthetic proposal" in current.text and "New synthetic proposal" in current.text
    eng._update_knowledge_item("lesson", old["id"], lambda row: {**row, "detail": "target changed"})
    result = ui().ReviewController(eng.root).decide(current, "approve")
    assert result["status"] == "version_conflict" and not result["changed"]
    assert eng._find_item_by_id(old["id"])[1]["tier"] == "verified"
    assert eng._find_item_by_id(new["id"])[1]["tier"] == "staging"


def test_second_click_cannot_reapply_or_reject_approved_item(eng):
    row = proposed(eng, "lesson")
    controller = ui().ReviewController(eng.root)
    current = card(eng, row)
    assert controller.decide(current, "approve")["ok"]
    before = snapshot(eng.root)
    assert not controller.decide(current, "approve")["changed"]
    assert not controller.decide(current, "reject")["changed"]
    assert snapshot(eng.root) == before


def test_reading_and_notices_do_not_approve_and_restart_recovers_queue(eng):
    row = proposed(eng, "lesson")
    cards = ui().ReviewController(eng.root).pending()
    before = snapshot(eng.root)
    notices = ui().PendingNotices()
    assert notices.observe(cards) == 1
    assert notices.observe(cards) == 0
    assert notices.observe(cards, enabled=False) == 0
    assert ui().PendingNotices().observe(cards) == 1
    assert ui().ReviewController(eng.root).pending()[0].item_id == row["id"]
    assert snapshot(eng.root) == before


def test_card_removes_controls_and_shows_entire_body(eng):
    row = eng.add_lesson({"summary": "Fake\u202eApprove\x1b[2J", "detail": "x" * 1500 + " END",
                           "domain": "type:lesson"})
    current = card(eng, row)
    assert "\u202e" not in current.text and "\x1b" not in current.text
    assert "x" * 1500 + " END" in current.text


def test_gui_entrypoint_is_packaged():
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["gui-scripts"]["piia-engram-review"] == "piia_engram.review_ui:main"


def test_native_window_reads_only_until_button_click(eng):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("native display unavailable")
    root.withdraw()
    try:
        row = proposed(eng, "lesson")
        before = snapshot(eng.root)
        window = ui().ReviewWindow(root, ui().ReviewController(eng.root), notify=False)
        window.refresh()
        assert snapshot(eng.root) == before
        window.select(0)
        assert "Synthetic proposal" in window.details.get("1.0", "end")
        window.later_button.invoke()
        assert snapshot(eng.root) == before
        window.select(0)
        window.approve_button.invoke()
        assert eng._find_item_by_id(row["id"])[1]["tier"] == "verified"
        assert row["id"] not in [c.item_id for c in window.controller.pending()]
    finally:
        root.destroy()


def test_forged_kind_cannot_decide_a_different_entry(eng):
    from dataclasses import replace
    row = proposed(eng, "lesson")
    current = replace(card(eng, row), kind="identity")
    before = snapshot(eng.root)
    assert ui().ReviewController(eng.root).decide(current, "approve")["status"] == "version_conflict"
    assert snapshot(eng.root) == before


def test_second_check_under_review_locks_catches_a_race(eng, monkeypatch):
    row = proposed(eng, "lesson")
    current = card(eng, row)
    controller = ui().ReviewController(eng.root)
    matches = controller._matches
    calls = 0
    def raced(store, displayed):
        nonlocal calls
        calls += 1
        if calls == 2:
            eng._update_knowledge_item("lesson", row["id"], lambda r: {**r, "detail": "race"})
        return matches(store, displayed)
    monkeypatch.setattr(controller, "_matches", raced)
    assert controller.decide(current, "approve")["status"] == "version_conflict"
    assert calls == 2 and eng._find_item_by_id(row["id"])[1]["tier"] == "staging"


def test_identity_interruption_recovers_only_after_fresh_display(eng, monkeypatch):
    from piia_engram import identity_review
    row = proposed(eng, "identity")
    controller = ui().ReviewController(eng.root)
    old_card = card(eng, row)
    save = identity_review._save
    def fault(store, rows):
        if any(r["status"] == "approved" for r in rows):
            raise OSError("synthetic failure")
        return save(store, rows)
    with monkeypatch.context() as patch:
        patch.setattr(identity_review, "_save", fault)
        failed = controller.decide(old_card, "approve")
        assert failed["status"] == "review_failed" and failed["changed"] is None
    assert eng.get_identity_proposals()[0]["status"] == "applying"
    assert controller.decide(old_card, "approve")["status"] == "version_conflict"
    fresh = card(eng, row)
    assert controller.decide(fresh, "approve")["ok"]
    assert eng.get_identity_proposals() == []


def test_root_is_explicit_not_selected_from_ambient_environment(eng, tmp_path, monkeypatch):
    row = proposed(eng, "lesson")
    other = tmp_path / "other-store"
    monkeypatch.setenv("ENGRAM_DIR", str(other))
    controller = ui().ReviewController(eng.root)
    assert controller.decide(card(eng, row), "approve")["ok"]
    assert not other.exists()


def test_argument_based_approval_is_refused_before_tk(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["piia-engram-review", "--approve", "synthetic-id"])
    assert ui().main() == 2


def test_mcp_hint_offers_local_window_without_approving(eng):
    from piia_engram import review_boundary
    row = proposed(eng, "lesson")
    current = card(eng, row)
    before = snapshot(eng.root)
    with write_provenance.origin_scope("mcp", client_name="owner"):
        refused = review_boundary.refusal(row["id"], action="approve")
        assert "piia-engram-review" in refused["hint"]
        assert not refused["changed"]
        assert ui().ReviewController(eng.root).decide(current, "approve")["status"] == "local_review_only"
    assert snapshot(eng.root) == before


def test_playbook_replacement_interruption_requires_fresh_owner_click(eng, monkeypatch):
    old = proposed(eng, "playbook")
    controller = ui().ReviewController(eng.root)
    assert controller.decide(card(eng, old), "approve")["ok"]
    new = eng.add_playbook({"title": "Revised synthetic procedure", "steps": [{"action": "replacement"}]},
                          _update_proposal_of=old["id"], allow_similar_new=True)
    original = card(eng, new)
    retire = Engram._retire_replaced_playbook
    def interrupted(store, old_id, new_id):
        retire(store, old_id, new_id)
        raise OSError("synthetic retirement interruption")
    with monkeypatch.context() as patch:
        patch.setattr(Engram, "_retire_replaced_playbook", interrupted)
        failed = controller.decide(original, "approve")
        assert failed["status"] == "review_failed" and failed["changed"] is None
    assert eng._read_playbook_by_id(old["id"])["status"] == "outdated"
    assert eng._read_playbook_by_id(new["id"])["tier"] == "staging"
    assert controller.decide(original, "approve")["status"] == "version_conflict"
    assert controller.decide(card(eng, new), "approve")["ok"]
    assert [p["id"] for p in eng.get_playbooks()] == [new["id"]]


def test_one_project_approval_leaves_other_project_pending(eng, tmp_path):
    first = eng.add_lesson({"summary": "Synthetic project A tool", "domain": "type:project_fact",
                            "project_folder": str(tmp_path / "project-a")})
    second = eng.add_lesson({"summary": "Different project B tool", "domain": "type:project_fact",
                             "project_folder": str(tmp_path / "project-b")})
    first_card = card(eng, first)
    assert "project:" in first_card.text
    controller = ui().ReviewController(eng.root)
    assert controller.decide(first_card, "approve")["ok"]
    assert eng._find_item_by_id(second["id"])[1]["tier"] == "staging"


def test_native_prompt_failure_leaves_queue_usable(eng, monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("native display unavailable")
    root.withdraw()
    try:
        row = proposed(eng, "lesson")
        def fail(_self):
            raise RuntimeError("synthetic prompt failure")
        monkeypatch.setattr(ui().ReviewWindow, "_notice", fail)
        before = snapshot(eng.root)
        window = ui().ReviewWindow(root, ui().ReviewController(eng.root))
        window.select(0)
        assert "Synthetic proposal" in window.details.get("1.0", "end")
        assert row["id"] in [c.item_id for c in window.controller.pending()]
        assert snapshot(eng.root) == before
        window.close()
    finally:
        try:
            root.destroy()
        except tkinter.TclError:
            pass


def test_native_refresh_and_notice_close_preserve_pending(eng):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("native display unavailable")
    root.withdraw()
    try:
        row = proposed(eng, "lesson")
        window = ui().ReviewWindow(root, ui().ReviewController(eng.root), notify=True)
        assert window.prompt.winfo_exists()
        notice_text = " ".join(str(w.cget("text")) for w in window.prompt.winfo_children())
        assert "Synthetic proposal" not in notice_text
        assert row["id"] not in notice_text
        window.prompt.destroy()
        window.select(0)
        old_text = window.details.get("1.0", "end")
        eng._update_knowledge_item("lesson", row["id"], lambda r: {**r, "detail": "new hidden value"})
        before = snapshot(eng.root)
        window.refresh()
        assert window.details.get("1.0", "end") == old_text
        assert str(window.approve_button.cget("state")) == "disabled"
        assert snapshot(eng.root) == before
        window.close()
        assert snapshot(eng.root) == before
        assert card(eng, row).item_id == row["id"]
    finally:
        try:
            root.destroy()
        except tkinter.TclError:
            pass
