"""Synthetic terminal and dangling admission intent lifecycle checks."""

import pytest

from piia_engram import write_provenance
from piia_engram.core import Engram
from piia_engram.isolated_store import GuardRefused, _sha256_json
from test_isolated_events import _admit
from test_isolated_store import ADMIT, RETIRE, _card, _make_world
from test_replay_experience import EARLY, _world


KEY = "source:1:lesson:v1"


def _interrupt(w, monkeypatch, *, after_write=False):
    if after_write:
        append = w.pr._append

        def stop(record):
            if record.get("op") == "admit" and record.get("result") == "admitted":
                raise SystemExit("synthetic completion interruption")
            return append(record)

        with monkeypatch.context() as patch:
            patch.setattr(w.pr, "_append", stop)
            with pytest.raises(SystemExit):
                _admit(w, KEY)
    else:
        def stop(*args, **kwargs):
            raise SystemExit("synthetic interruption before write")

        with monkeypatch.context() as patch:
            patch.setattr(Engram, "add_lesson", stop)
            with pytest.raises(SystemExit):
                _admit(w, KEY)
    return next(r for r in reversed(w.pr.receipts()) if r.get("op") == "admit_intent")


def _abandon(w, intent, **overrides):
    options = {"intent_receipt_seq": intent["seq"], "intent_receipt_sha256": _sha256_json(intent)}
    options.update(overrides)
    return w.pr.abandon_intent(KEY, **options)


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("result,code", [
    ({"status": "duplicate", "existing_id": "synthetic-twin"}, "duplicate"),
    ({"status": "duplicate_candidate", "error": "near duplicate"}, "duplicate_candidate"),
    ({"status": "duplicate_retired"}, "duplicate_retired"),
    ({"status": "rejected_before"}, "rejected_before"),
    ({"status": "capacity_full", "error": "full"}, "capacity_full"),
    ({"error": "review queue full"}, "queue_full"),
    ({"status": "event_key_conflict"}, "event_key_conflict"),
    ({"error": "synthetic write refusal"}, "write_error"),
])
def test_post_intent_refusal_has_referenced_terminal_and_releases_key(
        tmp_path, monkeypatch, mode, result, code):
    w = _world(tmp_path, monkeypatch, mode=mode)
    with monkeypatch.context() as patch:
        patch.setattr(Engram, "add_lesson", lambda *args, **kwargs: dict(result))
        refusal = _admit(w, KEY)
    assert refusal["result"] == code
    assert not {"intent_receipt_seq", "intent_receipt_sha256"} & refusal.keys()
    intent = w.pr.receipts()[-3]
    terminal = w.pr.receipts()[-1]
    assert terminal["op"] == "admit_intent_close" and terminal["result"] == "refused"
    assert terminal["intent_receipt_seq"] == intent["seq"]
    assert terminal["intent_receipt_sha256"] == _sha256_json(intent)
    assert terminal["refusal_result"] == code
    assert terminal["refusal_receipt_seq"] == refusal["seq"]
    assert terminal["refusal_receipt_sha256"] == _sha256_json(refusal)
    assert w.pr._rows(w.pr._engram(read_only=True)) == []
    assert w.pr.reconcile()["problems"] == []
    assert "repairs" not in w.pr.reconcile()
    changed = _card("changed", "Q2", EARLY)
    assert _admit(w, KEY, changed)["result"] == "admitted"
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("exception,code", [
    (OSError("synthetic OS refusal"), "io_retry_exhausted"),
    (GuardRefused("synthetic_validation_refused"), "synthetic_validation_refused"),
])
def test_expected_write_exceptions_close_intent(tmp_path, monkeypatch, exception, code):
    w = _make_world(tmp_path, monkeypatch)

    def refuse(*args, **kwargs):
        raise exception

    monkeypatch.setattr(Engram, "add_lesson", refuse)
    monkeypatch.setattr("piia_engram.isolated_store.IO_RETRY_SLEEP", 0)
    assert _admit(w, KEY)["result"] == code
    assert w.pr.receipts()[-1]["refusal_result"] == code
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("change,code", [
    ("missing", "not_verified_after_write"),
    ("tier", "not_verified_after_write"),
    ("evidence", "evidence_fields_missing"),
    ("key", "event_key_conflict"),
])
def test_post_write_validation_refusals_close_but_do_not_admit_rows(tmp_path, monkeypatch, change, code):
    w = _make_world(tmp_path, monkeypatch)
    find = Engram._find_item_by_id

    def changed_row(eng, item_id):
        kind, row = find(eng, item_id)
        row = dict(row)
        if change == "missing":
            row = None
        elif change == "tier":
            row["tier"] = "staging"
        elif change == "evidence":
            row["subject_id"] = "changed"
        else:
            row["event_key"] = "source:other:lesson:v1"
        return kind, row

    with monkeypatch.context() as patch:
        patch.setattr(Engram, "_find_item_by_id", changed_row)
        assert _admit(w, KEY)["result"] == code
    assert w.pr.receipts()[-1]["refusal_result"] == code
    report = w.pr.reconcile()
    assert any(p.startswith("row_without_receipt:") for p in report["problems"])
    assert not any(p.startswith("admission_") for p in report["problems"])
    assert "repairs" not in report


@pytest.mark.parametrize("preflight", ["admission", "card", "risk", "capacity"])
def test_preflight_refusals_write_no_intent(tmp_path, monkeypatch, preflight):
    w = _make_world(tmp_path, monkeypatch, limits={"soft_cap": 1, "hard_cap": 1})
    card = _card("sample", "Q2", EARLY)
    admission = ADMIT
    if preflight == "admission":
        admission = {}
    elif preflight == "card":
        card["summary"] = ""
    elif preflight == "risk":
        monkeypatch.setattr(Engram, "_assess_memory_risk", lambda *args: {"risk_level": "high"})
    else:
        assert _admit(w, "source:other:lesson:v1")["result"] == "admitted"
    before = len([r for r in w.pr.receipts() if r.get("op") == "admit_intent"])
    assert w.pr.admit(card, "round", admission, event_key=KEY)["result"] != "admitted"
    assert len([r for r in w.pr.receipts() if r.get("op") == "admit_intent"]) == before
    assert w.pr.reconcile()["problems"] == []


def test_existing_dangling_intent_closes_when_same_content_retry_hits_capacity(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"soft_cap": 1, "hard_cap": 1})
    intent = _interrupt(w, monkeypatch)
    assert _admit(w, "source:other:lesson:v1")["result"] == "admitted"
    assert _admit(w, KEY)["result"] == "capacity_full"
    assert w.pr.receipts()[-1]["intent_receipt_seq"] == intent["seq"]
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_dangling_intent_requires_explicit_abandon_for_changed_content(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    intent = _interrupt(w, monkeypatch)
    report = w.pr.reconcile()
    assert report["problems"] == [f"admission_intent_dangling:{intent['item_id']}"]
    assert "repairs" not in report
    assert report["abandons"] == [{"item_id": intent["item_id"], "op": "abandon_intent",
                                   "result": "abandon_if_no_row", "intent_receipt_seq": intent["seq"],
                                   "intent_receipt_sha256": _sha256_json(intent)}]
    changed = _card("changed", "Q2", EARLY)
    assert _admit(w, KEY, changed)["result"] == "event_key_conflict"
    assert w.pr.reconcile()["problems"] == report["problems"]
    assert _abandon(w, intent)["result"] == "abandoned"
    assert w.pr.reconcile()["problems"] == []
    assert _admit(w, KEY, changed)["result"] == "admitted"
    assert w.pr.reconcile()["problems"] == []
    assert _abandon(w, intent)["result"] == "intent_not_pending"


@pytest.mark.parametrize("reference", ["seq", "sha256"])
def test_abandon_refuses_stale_or_wrong_intent_reference(tmp_path, monkeypatch, reference):
    w = _make_world(tmp_path, monkeypatch)
    intent = _interrupt(w, monkeypatch)
    overrides = ({"intent_receipt_seq": intent["seq"] + 1} if reference == "seq" else
                 {"intent_receipt_sha256": "0" * 64})
    assert _abandon(w, intent, **overrides)["result"] == "intent_reference_conflict"
    assert w.pr.reconcile()["problems"] == [f"admission_intent_dangling:{intent['item_id']}"]
    assert _admit(w, KEY)["result"] == "admitted"
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("state", ["active", "retired", "archived"])
def test_abandon_refuses_any_existing_event_row(tmp_path, monkeypatch, state):
    w = _make_world(tmp_path, monkeypatch, limits={"r_max": 1})
    intent = _interrupt(w, monkeypatch, after_write=True)
    if state != "active":
        w.pr.retire(intent["item_id"], "round", RETIRE)
    if state == "archived":
        other = _admit(w, "source:other:lesson:v1")
        w.pr.retire(other["item_id"], "round", RETIRE)
        assert not any(r.get("id") == intent["item_id"] for r in w.pr._rows(w.pr._engram(read_only=True)))
    before = w.pr.state_hash()
    assert _abandon(w, intent)["result"] == "intent_has_row"
    assert w.pr.state_hash() == before


def test_abandon_is_refused_for_mcp_origin(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    intent = _interrupt(w, monkeypatch)
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        with pytest.raises(GuardRefused, match="isolated_event_parameters_not_supported"):
            _abandon(w, intent)
    assert w.pr.reconcile()["problems"] == [f"admission_intent_dangling:{intent['item_id']}"]


@pytest.mark.parametrize("wrong", ["seq", "sha256"])
def test_terminal_must_reference_exact_intent_to_close_it(tmp_path, monkeypatch, wrong):
    w = _make_world(tmp_path, monkeypatch)
    intent = _interrupt(w, monkeypatch)
    with w.pr.serial():
        w.pr._append({"op": "admit_intent_close", "result": "abandoned", "event_key": KEY,
                      "item_id": intent["item_id"],
                      "intent_receipt_seq": intent["seq"] + (wrong == "seq"),
                      "intent_receipt_sha256": "0" * 64 if wrong == "sha256" else _sha256_json(intent)})
    assert w.pr.reconcile()["problems"] == [f"admission_intent_dangling:{intent['item_id']}"]
    assert _abandon(w, intent)["result"] == "abandoned"


def test_old_unreferenced_refusal_does_not_silently_close_dangling_intent(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    intent = _interrupt(w, monkeypatch)
    with w.pr.serial():
        w.pr._append({"op": "admit", "result": "duplicate", "event_key": KEY, "item_id": "twin"})
    assert w.pr.reconcile()["problems"] == [f"admission_intent_dangling:{intent['item_id']}"]
    assert _abandon(w, intent)["result"] == "abandoned"
