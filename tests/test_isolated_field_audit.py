"""On-disk checks for synthetic sensitive inputs at isolated-store boundaries."""

import hashlib
import json

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import GuardRefused, IsolatedStore, RecallRefused, _sha256_json
from test_isolated_events import _admit
from test_isolated_intents import _interrupt
from test_isolated_store import ADMIT, LATER, RESTORE, RETIRE, _card, _make_world, _recall
from test_replay_experience import ADMITTED, CLOCK, CUT, EARLY, _world

MARKER = "SYNTHETIC private audit payload"


def _disk(w):
    paths = list(w.pr.receipts_dir.glob("*.jsonl"))
    paths += list((w.pr.root / "knowledge").rglob("*"))
    return {str(p.relative_to(w.area)): p.read_bytes() for p in paths if p.is_file()}


def _absent(w, marker=MARKER):
    for name, raw in _disk(w).items():
        assert marker.encode("utf-8") not in raw, name


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_query_is_digest_only_on_disk_and_returned_receipt(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    _admit(w, "source:1:lesson:v1")
    options = dict(evidence_before=CUT, admitted_before=LATER)
    if mode == "replay_experience":
        options.update(admitted_before=CUT, now=CLOCK)
    result = w.pr.recall("dp-replay", "round", query=MARKER, **options)
    _absent(w)
    sealed = json.loads(w.pr.receipts_path.read_text(encoding="utf-8").splitlines()[-1])
    assert result["receipt"] == sealed
    assert "query" not in sealed
    assert sealed["query_sha256"] == hashlib.sha256(MARKER.encode("utf-8")).hexdigest()
    assert sealed["query_term_count"] == 4
    assert IsolatedStore.open(w.cfg).verify_recall_receipt(sealed)["unchanged"]


@pytest.mark.parametrize("query,count", [(None, 0), ("", 0), (" \t ", 0), ("CACHE cache", 2)])
def test_query_digest_preserves_empty_and_matching_semantics(tmp_path, monkeypatch, query, count):
    w = _make_world(tmp_path, monkeypatch)
    _admit(w, "source:1:lesson:v1")
    result = _recall(w.pr, query=query)
    assert bool(result["items"]) == (query is None or count > 0)
    assert result["receipt"]["query_term_count"] == count
    assert "query" not in json.loads(w.pr.receipts_path.read_text(encoding="utf-8").splitlines()[-1])


def test_old_raw_query_ledger_stays_readable_without_rewriting(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    _admit(w, "source:1:lesson:v1")
    sealed = _recall(w.pr, query="cache")["receipt"]
    sealed.pop("query_term_count", None)
    sealed["query"] = "legacy sample query"
    lines = w.pr.receipts_path.read_text(encoding="utf-8").splitlines()
    lines[-1] = json.dumps(sealed)
    w.pr.receipts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = w.pr.receipts_path.read_bytes()
    reopened = IsolatedStore.open(w.cfg)
    assert reopened.verify_recall_receipt(sealed)["unchanged"]
    assert w.pr.receipts_path.read_bytes() == before
    _recall(reopened)
    assert reopened.receipts()[-1]["prev_sha256"] == _sha256_json(sealed)


@pytest.mark.parametrize("op", ["admit", "recall", "retire", "restore"])
def test_round_identifier_refuses_free_text_before_persistence(tmp_path, monkeypatch, op):
    w = _world(tmp_path, monkeypatch, mode="production")
    first = _admit(w, "source:1:lesson:v1")
    with pytest.raises((GuardRefused, RecallRefused, ValueError)):
        if op == "admit":
            w.pr.admit(_card("sample", "Q2", EARLY), MARKER, ADMIT)
        elif op == "recall":
            w.pr.recall("dp-live", MARKER, evidence_before=LATER, admitted_before=LATER)
        else:
            getattr(w.pr, op)(first["item_id"], MARKER, RETIRE if op == "retire" else RESTORE)
    _absent(w)


@pytest.mark.parametrize("field", ["subject_id", "source_decision_point", "source_family", "evidence_as_of"])
def test_card_metadata_and_invalid_time_never_echo_free_text(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch, mode="production")
    card = _card("sample", "Q2", EARLY)
    card[field] = MARKER
    receipt = w.pr.admit(card, "round", ADMIT, event_key="source:1:lesson:v1")
    _absent(w)
    assert receipt["result"] == "card_invalid"
    path = w.pr.root / "knowledge/lessons.json"
    assert not path.exists() or not json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("field", ["summary", "detail"])
def test_sensitive_card_bodies_are_refused_without_disk_copies(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch, mode="production")
    sensitive = "sk-" + "ant-api03-" + "S" * 40
    card = _card("sample", "Q2", EARLY)
    card[field] = sensitive
    receipt = w.pr.admit(card, "round", ADMIT, event_key="source:1:lesson:v1")
    assert receipt["result"] == "risk_refused"
    _absent(w, sensitive)


@pytest.mark.parametrize("field", ["judge_version", "decision_record_id", "reason", "detail"])
def test_admission_free_text_is_hashed_in_intents_and_lifecycle(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch)
    admission = dict(ADMIT, **{field: MARKER})
    first = w.pr.admit(_card("sample", "Q2", EARLY), "round", admission,
                       event_key="source:1:lesson:v1", admitted_before=ADMITTED, now=CLOCK)
    assert first["result"] == "admitted"
    for op, verdict, at in [("retire", RETIRE, "2020-01-02T12:00:00Z"),
                            ("restore", RESTORE, "2020-01-02T18:00:00Z")]:
        result = getattr(w.pr, op)(first["item_id"], "round", dict(verdict, **{field: MARKER}),
                                  effective_at=at, now=CLOCK)
        assert result["result"] in ("retired", "restored")
    _absent(w)
    ledger = [json.loads(line) for line in w.pr.receipts_path.read_text(encoding="utf-8").splitlines()]
    assert {"admit_intent", "admit", "retire", "restore"} <= {r["op"] for r in ledger}
    assert all(len(r["admission_sha256"]) == 64 for r in ledger if "admission_sha256" in r)


@pytest.mark.parametrize("field", ["kind", "mode", "family_code", "as_of_utc"])
def test_decision_file_fields_refuse_free_text_without_receipt(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch, mode="production")
    path = w.dps / "dp-live.json"
    dp = json.loads(path.read_text(encoding="utf-8"))
    dp[field] = MARKER
    path.write_text(json.dumps(dp), encoding="utf-8")
    with pytest.raises(RecallRefused):
        _recall(w.pr)
    _absent(w)


@pytest.mark.parametrize("field", ["subject_ids", "extra_exclude_families", "evidence_before", "admitted_before", "dp"])
def test_recall_identifiers_and_cuts_refuse_free_text(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch, mode="production")
    options = {field: [MARKER] if field in ("subject_ids", "extra_exclude_families") else MARKER}
    with pytest.raises((GuardRefused, RecallRefused, ValueError)):
        _recall(w.pr, **options)
    _absent(w)


@pytest.mark.parametrize("field", ["effective_at", "now", "admitted_before"])
def test_logical_clock_refusals_do_not_persist_invalid_inputs(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch)
    if field == "admitted_before":
        result = w.pr.admit(_card("sample", "Q2", EARLY), "round", ADMIT,
                            event_key="source:1:lesson:v1", admitted_before=MARKER, now=CLOCK)
    else:
        first = _admit(w, "source:1:lesson:v1")
        options = dict(effective_at="2020-01-02T12:00:00Z", now=CLOCK)
        options[field] = MARKER
        result = w.pr.retire(first["item_id"], "round", RETIRE, **options)
    assert result["result"].endswith("_invalid")
    _absent(w)


@pytest.mark.parametrize("op", ["admit", "abandon_intent"])
def test_invalid_event_key_has_no_disk_copy(tmp_path, monkeypatch, op):
    w = _world(tmp_path, monkeypatch, mode="production")
    if op == "admit":
        assert _admit(w, MARKER)["result"] == "event_key_invalid"
    else:
        with pytest.raises(GuardRefused):
            w.pr.abandon_intent(MARKER, intent_receipt_seq=1, intent_receipt_sha256="0" * 64)
    _absent(w)


def test_abandon_reference_inputs_are_not_persisted(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    intent = _interrupt(w, monkeypatch)
    assert w.pr.abandon_intent(intent["event_key"], intent_receipt_seq=MARKER,
                              intent_receipt_sha256=MARKER)["result"] == "intent_reference_conflict"
    assert w.pr.abandon_intent(intent["event_key"], intent_receipt_seq=intent["seq"],
                              intent_receipt_sha256=_sha256_json(intent))["result"] == "abandoned"
    _absent(w)
    ledger = w.pr.receipts_path.read_text(encoding="utf-8")
    assert '"result": "abandoned"' in ledger


@pytest.mark.parametrize("op", ["retire", "restore"])
def test_item_identifier_rejects_free_text(tmp_path, monkeypatch, op):
    w = _world(tmp_path, monkeypatch, mode="production")
    with pytest.raises(GuardRefused):
        getattr(w.pr, op)(MARKER, "round", RETIRE if op == "retire" else RESTORE)
    _absent(w)


def test_variable_error_details_are_digest_only_in_ledger_and_refusals(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    with monkeypatch.context() as patch:
        patch.setattr(Engram, "archive_lesson", lambda *args: {"error": MARKER})
        result = w.pr.retire(first["item_id"], "round", RETIRE,
                             effective_at="2020-01-02T12:00:00Z", now=CLOCK)
    assert result["result"] == "write_error"
    refusal = w.pr._refuse_unreadable("recall", MARKER)
    _absent(w)
    assert result["detail_sha256"] == refusal["detail_sha256"] == hashlib.sha256(MARKER.encode("utf-8")).hexdigest()
    assert refusal == json.loads((w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8"))


def test_verification_is_body_free_and_writes_no_caller_receipt(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    _admit(w, "source:1:lesson:v1")
    sealed = _recall(w.pr)["receipt"]
    before = _disk(w)
    result = w.pr.verify_recall_receipt(dict(sealed, query=MARKER, detail=MARKER))
    assert result == {"result": "recall_receipt_invalid", "unchanged": False, "changes": []}
    assert w.pr.verify_recall_receipt(sealed)["unchanged"]
    assert _disk(w) == before
    _absent(w)


@pytest.mark.parametrize("op", ["owner_retire", "owner_reject", "owner_clear_latch", "owner_rebind"])
def test_owner_operator_is_a_constrained_identifier(tmp_path, monkeypatch, op):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    with pytest.raises(GuardRefused):
        if op in ("owner_retire", "owner_reject"):
            getattr(w.pr, op)(first["item_id"], MARKER)
        else:
            getattr(w.pr, op)(MARKER)
    _absent(w)


def test_guard_refusals_store_codes_without_sensitive_clock(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    with pytest.raises(GuardRefused, match="clock_invalid"):
        w.pr.recall("dp-replay", "round", evidence_before=CUT, admitted_before=CUT, now=MARKER)
    _absent(w)
    refusal = json.loads((w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8"))
    assert set(refusal) == {"op", "result", "code", "ts", "pid"}


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_limit_configuration_cannot_add_text_to_receipts(tmp_path, monkeypatch, mode):
    with pytest.raises(GuardRefused, match="guard_limits_invalid"):
        _world(tmp_path, monkeypatch, mode=mode, limits={"comment": MARKER})
    for path in (tmp_path / "caller_area/receipts").glob("*.jsonl"):
        assert MARKER.encode("utf-8") not in path.read_bytes()


@pytest.mark.parametrize("key", ["x", "x" * 256])
def test_valid_event_key_boundaries_are_identical_in_ledger_and_library(tmp_path, monkeypatch, key):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, key)
    assert first["result"] == "admitted"
    ledger = [json.loads(line) for line in w.pr.receipts_path.read_text(encoding="utf-8").splitlines()]
    rows = json.loads((w.pr.root / "knowledge/lessons.json").read_text(encoding="utf-8"))
    assert rows[0]["event_key"] == first["event_key"] == key
    assert all(r["event_key"] == key for r in ledger if "event_key" in r)
    assert first["item_id"] == rows[0]["id"] == hashlib.sha256(key.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("key", ["x" * 257, "x\n", "x 文本"])
def test_invalid_event_key_boundaries_never_reach_disk(tmp_path, monkeypatch, key):
    w = _make_world(tmp_path, monkeypatch)
    assert _admit(w, key)["result"] == "event_key_invalid"
    _absent(w, key)


def test_unused_card_text_is_not_copied_to_library_or_intents(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    card = dict(_card("sample", "Q2", EARLY), notes=MARKER, reasoning=MARKER, arbitrary=MARKER)
    assert _admit(w, "source:1:lesson:v1", card)["result"] == "admitted"
    _absent(w)
