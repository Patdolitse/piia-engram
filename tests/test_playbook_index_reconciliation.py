"""The playbook body is authoritative; repairs preserve bodies and review state."""

import pytest

from piia_engram import setup_wizard
from piia_engram import doctor, pinning
from piia_engram.core import Engram
from piia_engram.storage import ReadOnlyStoreError


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _stale_pair(tmp_path, monkeypatch):
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    old = eng.add_playbook({"id": "_custom", "title": "Legacy release checklist", "steps": ["old"]})
    new = eng.add_playbook({"title": "Replacement checklist", "steps": ["new"]})
    pinning.pin(eng, old["id"])
    # Simulate a crash/older store: retired body, stale pin, active index.
    path = eng._playbook_path(old["id"])
    row = eng._read_playbook_by_id(old["id"])
    row["status"] = "outdated"
    eng._write_playbook_file(path, row)
    eng._update_playbook_file_by_id(new["id"], lambda r: {**r, "tier": "staging", "approval_status": "pending"})
    return eng, old, new


def test_reconciliation_reports_without_writes_and_repairs_only_requested_body(tmp_path, monkeypatch):
    eng, old, new = _stale_pair(tmp_path, monkeypatch)
    reader = Engram(root=eng.root, read_only=True)
    before = _snapshot(eng.root)
    report = reader._reconcile_playbook_index(dry_run=True)
    assert report["mismatches"] >= 2
    assert _snapshot(eng.root) == before
    with pytest.raises(ReadOnlyStoreError):
        reader._reconcile_playbook_index()
    eng._reconcile_playbook_index(old["id"])
    index = {r["id"]: r for r in eng._read_playbook_index()}
    assert index[old["id"]]["status"] == "outdated"
    assert not pinning.has_pin(eng._read_playbook_by_id(old["id"]))
    assert index[new["id"]]["tier"] == "verified"
    eng._reconcile_playbook_index()
    assert {r["id"]: r for r in eng._read_playbook_index()}[new["id"]]["tier"] == "staging"
    before = _snapshot(eng.root)
    eng._reconcile_playbook_index()
    assert _snapshot(eng.root) == before
    assert eng._playbook_path(old["id"]).exists() and eng._playbook_path(new["id"]).exists()


@pytest.mark.parametrize("fix", [False, True])
def test_doctor_reconciles_even_without_detected_clients(tmp_path, monkeypatch, capsys, fix):
    eng, old, new = _stale_pair(tmp_path, monkeypatch)
    eng._update_playbook_file_by_id(new["id"], lambda r: {**r, "tier": "verified", "approval_status": "approved"})
    monkeypatch.setattr(doctor, "_detect_installed_tools", lambda: [])
    monkeypatch.setattr(setup_wizard, "_configure_utf8_stdio", lambda: None)
    before = _snapshot(eng.root)
    doctor.run_doctor(fix=fix)
    assert "playbook index" in capsys.readouterr().out.lower()
    if not fix:
        assert _snapshot(eng.root) == before
    else:
        index = {r["id"]: r for r in eng._read_playbook_index()}
        assert index[old["id"]]["status"] == "outdated"
        assert not pinning.has_pin(eng._read_playbook_by_id(old["id"]))
        for item_id in (old["id"], new["id"]):
            body = eng._read_playbook_by_id(item_id)
            assert index[item_id]["status"] == body["status"]
            assert index[item_id]["tier"] == body["tier"]
        assert {r["id"] for r in eng.get_playbooks()} == {new["id"]}


def test_doctor_can_retry_an_interrupted_index_repair(tmp_path, monkeypatch, capsys):
    eng, old, _new = _stale_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor, "_detect_installed_tools", lambda: [])
    monkeypatch.setattr(setup_wizard, "_configure_utf8_stdio", lambda: None)
    with monkeypatch.context() as fault:
        fault.setattr(Engram, "_update_playbook_index", lambda *_: (_ for _ in ()).throw(OSError("index interrupted")))
        assert doctor.run_doctor(fix=True) >= 1
    assert "index" in capsys.readouterr().out.lower()
    assert eng._playbook_path(old["id"]).exists()
    assert doctor.run_doctor(fix=True) == 0
    index = {r["id"]: r for r in eng._read_playbook_index()}
    for item_id in (old["id"], _new["id"]):
        body = eng._read_playbook_by_id(item_id)
        assert index[item_id]["status"] == body["status"]
        assert index[item_id]["tier"] == body["tier"]
    assert index[old["id"]]["status"] == "outdated"


@pytest.mark.parametrize("mode", ["apply", "preview", "unconfirmed", "stale"])
def test_batch_approval_retry_reconciles_only_after_confirmation_and_version_guard(tmp_path, monkeypatch, mode):
    from piia_engram.staging_review import batch_review_staging

    eng, old, new = _stale_pair(tmp_path, monkeypatch)
    eng._update_playbook_file_by_id(new["id"], lambda r: {
        **r, "tier": "verified", "approval_status": "approved", "pending_supersedes": old["id"]})
    before = _snapshot(eng.root)
    result = batch_review_staging(eng, [{"id": new["id"], "action": "approve",
                                       "expected_version": 99 if mode == "stale" else 1}],
                                 dry_run=mode == "preview", confirm=mode != "unconfirmed", owner_cli=True)
    if mode != "apply":
        assert _snapshot(eng.root) == before
        if mode == "stale":
            assert result["items"][0]["status"] == "version_conflict"
    else:
        index = {r["id"]: r for r in eng._read_playbook_index()}
        assert index[old["id"]]["status"] == "outdated"
        assert index[new["id"]]["tier"] == "verified"
        assert result["changed"]
        assert {r["id"] for r in eng.get_playbooks()} == {new["id"]}


def test_reconciliation_restores_unindexed_body_without_removing_missing_body_entry(tmp_path):
    eng = Engram(root=tmp_path / "store")
    row = eng.add_playbook({"title": "Orphaned index recovery", "steps": ["keep body"]})
    body = eng._playbook_path(row["id"]).read_bytes()
    missing = {"id": "missing-body", "status": "active", "tier": "verified"}
    eng._update_playbook_index(lambda _: [missing])
    report = eng._reconcile_playbook_index()
    assert report["skipped"] == 1
    assert eng._playbook_path(row["id"]).read_bytes() == body
    index = {r["id"]: r for r in eng._read_playbook_index()}
    assert index["missing-body"] == missing
    assert index[row["id"]]["status"] == "active"
    assert index[row["id"]]["tier"] == "verified"


@pytest.mark.parametrize("damaged", ["body", "index"])
def test_doctor_report_does_not_quarantine_invalid_playbook_json(tmp_path, monkeypatch, capsys, damaged):
    eng, old, _new = _stale_pair(tmp_path, monkeypatch)
    path = eng._playbook_path(old["id"]) if damaged == "body" else eng._playbooks_dir / "_index.json"
    path.write_text("{invalid-json", encoding="utf-8")
    monkeypatch.setattr(doctor, "_detect_installed_tools", lambda: [])
    monkeypatch.setattr(setup_wizard, "_configure_utf8_stdio", lambda: None)
    before = _snapshot(eng.root)
    assert doctor.run_doctor(fix=False) >= 1
    assert _snapshot(eng.root) == before
    assert "invalid-json" not in capsys.readouterr().out


def test_reconciliation_mcp_refusal_is_byte_identical(tmp_path, monkeypatch):
    from piia_engram import review_boundary

    eng, _old, _new = _stale_pair(tmp_path, monkeypatch)
    before = _snapshot(eng.root)
    monkeypatch.setattr(review_boundary, "mcp_origin", lambda: True)
    assert eng._reconcile_playbook_index()["error"] == "local_review_only"
    assert _snapshot(eng.root) == before


def test_reconciliation_keeps_encrypted_body_and_noop_index_bytes(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-reconciliation-secret")
    eng, old, new = _stale_pair(tmp_path, monkeypatch)
    body = eng._playbook_path(new["id"]).read_bytes()
    assert b"enc:v2c:" in body
    eng._reconcile_playbook_index()
    assert eng._playbook_path(new["id"]).read_bytes() == body
    assert not pinning.has_pin(eng._read_playbook_by_id(old["id"]))
    before = _snapshot(eng.root)
    assert not eng._reconcile_playbook_index()["changed"]
    assert _snapshot(eng.root) == before
