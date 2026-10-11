"""Synthetic event identity checks for automated isolated callers."""

import inspect
import json

import pytest

from piia_engram import mcp_server, write_provenance
from piia_engram.core import Engram
from piia_engram.isolated_store import GuardRefused
from test_isolated_store import ADMIT, LATER, RESTORE, RETIRE, _card, _make_world, _recall
from test_replay_experience import ADMITTED, CLOCK, EARLY, _world


def _admit(w, key, card=None):
    options = {"event_key": key}
    if w.pr.mode == "replay_experience":
        options.update(admitted_before=ADMITTED, now=CLOCK)
    return w.pr.admit(card or _card("sample", "Q2", EARLY), "round", ADMIT, **options)


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_event_retry_conflict_and_identical_text_with_distinct_keys(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    first = _admit(w, "source:1:lesson:v1")
    assert first["result"] == "admitted"
    retry = _admit(w, "source:1:lesson:v1")
    assert retry["result"] == "already_admitted"
    assert retry["item_id"] == first["item_id"]
    assert retry["original_receipt_seq"] == first["seq"]
    assert retry["content_sha256"] == first["content_sha256"]
    assert retry == w.pr.receipts()[-1]
    changed = _card("sample", "Q2", EARLY)
    changed["detail"] += " corrected"
    before = w.pr.state_hash()
    assert _admit(w, "source:1:lesson:v1", changed)["result"] == "event_key_conflict"
    assert w.pr.state_hash() == before
    other = _admit(w, "source:2:lesson:v1")
    assert other["result"] == "admitted" and other["item_id"] != first["item_id"]
    assert [r["event_key"] for r in w.pr._rows(w.pr._engram(read_only=True))] == [
        "source:1:lesson:v1", "source:2:lesson:v1"]
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("key", ["", " ", "a b", "x" * 257, "a\n", 1, True, [], "路径"])
def test_event_key_validation_writes_no_row(tmp_path, monkeypatch, key):
    w = _make_world(tmp_path, monkeypatch)
    before = w.pr.state_hash()
    assert _admit(w, key)["result"] == "event_key_invalid"
    assert w.pr.state_hash() == before


def test_600_template_events_are_verified_and_recallable_in_production(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"soft_cap": 600, "hard_cap": 600})
    ids = []
    for ordinal in range(600):
        card = _card("sample", "Q2", EARLY, summary=f"Cache observation for event {ordinal}")
        receipt = _admit(w, f"source:{ordinal}:lesson:v1", card)
        assert receipt["result"] == "admitted", (ordinal, receipt["result"])
        ids.append(receipt["item_id"])
    rows = w.pr._rows(w.pr._engram(read_only=True))
    assert len(rows) == 600 and all(r["tier"] == "verified" for r in rows)
    assert all("duplicate_candidate" not in r for r in rows)
    assert {r["id"] for r in _recall(w.pr, limit=600)["items"]} == set(ids)
    # A retry must also succeed when the store is at capacity.
    assert _admit(w, "source:0:lesson:v1", _card(
        "sample", "Q2", EARLY, summary="Cache observation for event 0"))["result"] == "already_admitted"
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_mixed_keyed_and_unkeyed_admissions_preserve_legacy_duplicate_gate(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    assert _admit(w, None)["result"] == "admitted"
    assert _admit(w, "source:1:lesson:v1")["result"] == "admitted"
    assert _admit(w, None)["result"] == "duplicate"
    assert "event_key" not in w.pr.receipts()[-1]


@pytest.mark.parametrize("action,code", [("retire", "duplicate_retired"),
                                        ("owner_retire", "owner_vetoed"),
                                        ("owner_reject", "owner_vetoed")])
def test_event_lifecycle_blocks_only_its_key(tmp_path, monkeypatch, action, code):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    if action == "retire":
        w.pr.retire(first["item_id"], "round", RETIRE)
    else:
        getattr(w.pr, action)(first["item_id"], "Owner")
    assert _admit(w, "source:1:lesson:v1")["result"] == code
    assert _admit(w, "source:2:lesson:v1")["result"] == "admitted"
    if action == "retire":
        assert w.pr.restore(first["item_id"], "round", RESTORE)["result"] == "restored"
        assert _admit(w, "source:1:lesson:v1")["result"] == "already_admitted"
    else:
        assert len(_recall(w.pr)["items"]) == 1


def test_internal_event_parameter_is_unavailable_to_ordinary_store_and_mcp(tmp_path, monkeypatch):
    normal = Engram(root=tmp_path / "ordinary")
    with pytest.raises(GuardRefused, match="isolated_event_parameters_not_supported"):
        normal.add_lesson("Cache event", _isolated_event_key="source:1:lesson:v1")
    w = _make_world(tmp_path / "isolated", monkeypatch)
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        with pytest.raises(GuardRefused, match="isolated_event_parameters_not_supported"):
            w.pr._engram(read_only=False).add_lesson("Cache event", _isolated_event_key="source:1:lesson:v1")
        assert _admit(w, "source:1:lesson:v1")["result"] == "isolated_event_parameters_not_supported"
    assert "_isolated_event_key" not in inspect.signature(mcp_server.add_lesson).parameters
    assert "event_key" not in inspect.signature(mcp_server.add_lesson).parameters
    assert w.pr._rows(w.pr._engram(read_only=True)) == []


def test_payload_cannot_enable_internal_event_option(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    summary = "Cache event observation with repeated reads of the same bounded sample"
    assert eng.add_lesson({"summary": summary, "tier": "verified"})["tier"] == "verified"
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        result = eng.add_lesson({"summary": summary + " revised", "tier": "verified",
                                 "_isolated_event_key": "source:2:lesson:v1"})
    assert result["tier"] == "staging" and result["duplicate_candidate"]


@pytest.mark.parametrize("change,problem", [("key", "event_key_mismatch"),
                                           ("content", "content_changed"),
                                           ("duplicate", "duplicate_event_key")])
def test_reconcile_reports_event_identity_drift(tmp_path, monkeypatch, change, problem):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    second = _admit(w, "source:2:lesson:v1")
    path = w.pr.root / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if change == "content":
        rows[0]["detail"] += " changed"
    else:
        rows[0]["event_key"] = rows[1]["event_key"] if change == "duplicate" else "source:3:lesson:v1"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert any(p.startswith(problem + ":") for p in w.pr.reconcile()["problems"])
    assert first["item_id"] != second["item_id"]


def test_reconcile_reports_duplicate_event_keys_in_ledger(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    second = _admit(w, "source:2:lesson:v1")
    with w.pr.serial():
        w.pr._append({**{k: v for k, v in second.items() if k not in (
            "seq", "prev_sha256", "ts", "root_state_sha256")}, "event_key": first["event_key"]})
    assert any(p.startswith("duplicate_event_key_receipt:") for p in w.pr.reconcile()["problems"])


def test_retry_refuses_unreceipted_duplicate_key_row(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    path = w.pr.root / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.append(dict(rows[0], id="orphan"))
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert _admit(w, "source:1:lesson:v1")["result"] == "event_key_conflict"


def test_retry_uses_the_same_repaired_content_hash_as_admission(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    summary = "Sample: " + "发布流程测试".encode("utf-8").decode("gbk") + " cache observation"
    card = _card("sample", "Q2", EARLY, summary=summary)
    first = _admit(w, "source:1:lesson:v1", card)
    assert first["result"] == "admitted"
    assert _admit(w, "source:1:lesson:v1", card)["result"] == "already_admitted"


def test_archived_event_key_stays_reserved_and_can_restore_original_item(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"r_max": 1})
    first = _admit(w, "source:1:lesson:v1")
    second = _admit(w, "source:2:lesson:v1")
    w.pr.retire(first["item_id"], "round", RETIRE)
    w.pr.retire(second["item_id"], "round", RETIRE)
    eng = w.pr._engram(read_only=True)
    archived = next(r for r in eng._read_overflow_archive("lesson") if not eng._is_snapshot_record(r))
    assert _admit(w, archived["event_key"])["result"] == "duplicate_retired"
    assert _admit(w, "source:3:lesson:v1")["result"] == "admitted"
    assert w.pr.restore(archived["id"], "round", RESTORE)["result"] == "restored"
    assert _admit(w, archived["event_key"])["result"] == "already_admitted"
    assert w.pr.reconcile()["problems"] == []
