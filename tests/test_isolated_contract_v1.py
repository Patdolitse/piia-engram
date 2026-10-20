"""Frozen v1 surface for automated evaluation callers; synthetic stores only."""

import inspect

import pytest

from piia_engram import isolated_store as iso
from test_isolated_store import ADMIT, RESTORE, RETIRE, LATER, _card, _make_world, _recall


ENVELOPE = set("seq prev_sha256 ts pid proc_started_utc lib_version limits root_state_sha256".split())
ADMISSION = set("op round_id admission_sha256 subject_id evidence_as_of source_family result item_id content_sha256".split())
RECALL = set("op round_id result decision_point_id decision_point_kind decision_point_mode query_sha256 evidence_before admitted_before effective_evidence_before effective_admitted_before returned_ids excluded snapshot_id snapshot returned_content_sha256 as_of_utc root_mode limit query_term_count excluded_families subject_ids omitted result_sha256".split())


def test_version_pin_is_readable_and_unsupported_refuses_without_io(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    before = w.pr.receipts_path.read_bytes()
    assert iso.CONTRACT_VERSION == "1"
    assert iso.require_contract("1") == "1"
    for version in ("0", "2", 1, None):
        with pytest.raises(iso.GuardRefused) as exc:
            iso.require_contract(version)
        assert exc.value.code == "contract_version_unsupported"
    assert w.pr.receipts_path.read_bytes() == before


@pytest.mark.parametrize("method,parameters", [
    ("open", "cfg=None,*,allow_strict_latch=False,allow_rebind=False"),
    ("admit", "self,card,round_id,admission,*,admitted_before=None,now=None,event_key=None,supersedes=None"),
    ("retire", "self,item_id,round_id,admission,*,_op='retire',effective_at=None,now=None"),
    ("restore", "self,item_id,round_id,admission,*,effective_at=None,now=None"),
    ("receipts", "self"),
    ("recall", "self,decision_point_id,round_id,*,evidence_before,admitted_before,extra_exclude_families=(),subject_ids=None,query=None,limit=8,now=None"),
    ("state_hash", "self"),
    ("verify_recall_receipt", "self,receipt"),
    ("reconcile", "self"),
    ("abandon_intent", "self,event_key,*,intent_receipt_seq,intent_receipt_sha256"),
])
def test_public_signatures(method, parameters):
    signature = inspect.signature(getattr(iso.IsolatedStore, method))
    # Pin names, kinds, order and defaults independently of annotation rendering.
    actual = str(signature.replace(parameters=[p.replace(annotation=inspect.Parameter.empty)
                 for p in signature.parameters.values()], return_annotation=inspect.Signature.empty))
    assert actual.replace(" ", "") == f"({parameters})"


def test_public_annotations_and_version_helper_signature():
    common = {"self": inspect.Parameter.empty}
    expected = {
        "open": ({"cfg": "Config | None", "allow_strict_latch": "bool", "allow_rebind": "bool"}, "'IsolatedStore'"),
        "admit": ({**common, "card": "dict", "round_id": "str", "admission": "dict", "admitted_before": "str | None",
                   "now": "str | None", "event_key": "str | None", "supersedes": "str | None"}, "dict"),
        "retire": ({**common, "item_id": "str", "round_id": "str", "admission": "dict", "_op": "str",
                    "effective_at": "str | None", "now": "str | None"}, "dict"),
        "restore": ({**common, "item_id": "str", "round_id": "str", "admission": "dict",
                     "effective_at": "str | None", "now": "str | None"}, "dict"),
        "receipts": (common, "list[dict]"), "state_hash": (common, "str"),
        "reconcile": (common, "dict"), "verify_recall_receipt": ({**common, "receipt": "dict"}, "dict"),
        "abandon_intent": ({**common, "event_key": "str", "intent_receipt_seq": "int", "intent_receipt_sha256": "str"}, "dict"),
        "recall": ({**common, "decision_point_id": "str", "round_id": "str", "evidence_before": "str",
                    "admitted_before": "str", "extra_exclude_families": "Iterable[str]",
                    "subject_ids": "Iterable[str] | None", "query": "str | None", "limit": "int", "now": "str | None"}, "dict"),
    }
    for name, (annotations, returns) in expected.items():
        signature = inspect.signature(getattr(iso.IsolatedStore, name))
        assert {key: p.annotation for key, p in signature.parameters.items()} == annotations
        assert signature.return_annotation == returns
    assert str(inspect.signature(iso.require_contract)) == "(version: 'str') -> 'str'"


def test_operation_and_result_field_sets(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    first = w.pr.admit(_card("a", "Q2", "2020-01-01T00:00:00Z"), "round", ADMIT)
    assert set(first) == ENVELOPE | ADMISSION
    missing = w.pr.admit({}, "round", {})
    assert set(missing) == ENVELOPE | {"op", "round_id", "result"}
    for method, verdict, expected in (("retire", RETIRE, "retired"), ("restore", RESTORE, "restored")):
        receipt = getattr(w.pr, method)(first["item_id"], "round", verdict)
        assert receipt["result"] == expected
        assert set(receipt) == ENVELOPE | {"op", "round_id", "item_id", "admission_sha256", "result"}
    recalled = _recall(w.pr)
    assert set(recalled) == {"items", "excluded", "mode", "receipt", "excluded_families",
                             "effective_evidence_before", "effective_admitted_before", "result_sha256"}
    assert set(recalled["receipt"]) == ENVELOPE | RECALL
    assert set(recalled["items"][0]) == {"id", "subject_id", "summary", "detail", "evidence_as_of",
                                         "source_family", "admitted_round"}
    assert set(w.pr.verify_recall_receipt(recalled["receipt"])) == {"result", "snapshot_id", "unchanged", "changes"}
    assert set(w.pr.verify_recall_receipt({})) == {"result", "unchanged", "changes"}
    assert set(w.pr.reconcile()) == {"problems", "receipts", "rows", "state_sha256"}
    assert w.pr.receipts()[-1] == recalled["receipt"]


def test_filtering_cuts_retirement_veto_families_and_order(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    receipts = {}
    for key, family, evidence in (("a", "Q2", "2020-01-01T00:00:00Z"),
                                  ("b", "Q2", "2020-01-01T00:00:00Z"),
                                  ("c", "Q3", "2020-01-02T00:00:00Z"),
                                  ("future", "Q2", "2020-02-01T00:00:00Z"),
                                  ("own", "Q1", "2020-01-01T00:00:00Z"),
                                  ("veto", "Q2", "2020-01-01T00:00:00Z")):
        receipts[key] = w.pr.admit(_card(key, family, evidence), "round", ADMIT, event_key=f"sample:{key}")
        assert receipts[key]["result"] == "admitted"
    w.pr.retire(receipts["c"]["item_id"], "round", RETIRE)
    w.pr.owner_retire(receipts["veto"]["item_id"], "Owner")
    result = _recall(w.pr, dp="dp-test", evidence_before="2020-02-01T00:00:00Z")
    assert [r["id"] for r in result["items"]] == sorted([receipts[k]["item_id"] for k in ("a", "b")], reverse=True)
    assert result["excluded"] == {"family_excluded": 1, "evidence_after": 1}
    assert result["receipt"]["omitted"]["owner_veto"] == 1
    assert result["receipt"]["omitted"]["retired"] == 1
    assert _recall(w.pr, admitted_before=receipts["a"]["ts"])["items"] == []
    assert _recall(w.pr, extra_exclude_families=["q2"])["items"] == [
        r for r in _recall(w.pr)["items"] if r["source_family"] != "Q2"]
    assert _recall(w.pr, subject_ids=["a"], query="CACHE")["items"][0]["subject_id"] == "a"
    assert _recall(w.pr, query=" ")["items"] == []
    assert _recall(w.pr, limit=0)["items"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_keyed_supersede_and_replay_receipt_field_sets(tmp_path, monkeypatch, mode):
    from test_isolated_supersede import T2, admit, recall
    from test_replay_experience import _world

    w = _world(tmp_path, monkeypatch, mode=mode)
    mode_fields = {"store_mode", "admitted_before", "clock"} if mode == "replay_experience" else set()
    envelope = ENVELOPE | ({"store_mode"} if mode == "replay_experience" else set())
    assert set(w.pr.receipts()[0]) == envelope | {"op", "result", "store_mode", "root_id"} | (
        {"replay_marker", "replay_marker_sha256"} if mode == "replay_experience" else set())
    old = admit(w, 1, key="sample:v1")
    intent = w.pr.receipts()[-2]
    assert set(intent) == ENVELOPE | ADMISSION | mode_fields | {"event_key", "root_id"}
    assert set(old) == set(intent) | {"intent_receipt_seq", "intent_receipt_sha256"}
    new = admit(w, 2, key="sample:v2", supersedes=old["item_id"], at=T2)
    supersede_intent = w.pr.receipts()[-2]
    assert set(supersede_intent) == set(intent) | {"supersedes", "superseded_by"}
    assert set(new) == set(old) | {"supersedes", "superseded_by"}
    retry = admit(w, 2, key="sample:v2", supersedes=old["item_id"], at=T2)
    assert set(retry) == ENVELOPE | ADMISSION | mode_fields | {"event_key", "supersedes", "superseded_by",
                           "original_receipt_seq", "original_receipt_sha256"}
    recalled = recall(w)
    assert set(recalled["receipt"]) == envelope | RECALL | ({"clock"} if mode == "replay_experience" else set())
    assert set(recalled["items"][0]) == {"id", "subject_id", "summary", "detail", "evidence_as_of",
                                         "source_family", "admitted_round"} | (
        {"store_mode"} if mode == "replay_experience" else set())


def test_intent_close_and_abandon_result_field_sets(tmp_path, monkeypatch):
    from test_isolated_intents import KEY, _abandon, _interrupt

    w = _make_world(tmp_path, monkeypatch)
    intent = _interrupt(w, monkeypatch)
    report = w.pr.reconcile()
    assert set(report) == {"problems", "receipts", "rows", "state_sha256", "abandons"}
    assert set(report["abandons"][0]) == {"item_id", "op", "result", "intent_receipt_seq", "intent_receipt_sha256"}
    wrong = _abandon(w, intent, intent_receipt_seq=0)
    assert set(wrong) == ENVELOPE | {"op", "result", "event_key", "item_id"}
    closed = _abandon(w, intent)
    assert set(closed) == ENVELOPE | {"op", "result", "event_key", "item_id", "intent_receipt_seq", "intent_receipt_sha256"}
    missing = w.pr.abandon_intent(KEY, intent_receipt_seq=intent["seq"], intent_receipt_sha256=iso._sha256_json(intent))
    assert set(missing) == ENVELOPE | {"op", "result", "event_key"}


@pytest.mark.parametrize("method,admission", [("retire", RETIRE), ("restore", RESTORE)])
@pytest.mark.parametrize("failure", ["missing_admission", "not_found", "time_not_supported"])
def test_lifecycle_refusal_field_sets(tmp_path, monkeypatch, method, admission, failure):
    w = _make_world(tmp_path, monkeypatch)
    options = {"effective_at": "2020-01-01T00:00:00Z", "now": LATER} if failure == "time_not_supported" else {}
    result = getattr(w.pr, method)("missing", "round", {} if failure == "missing_admission" else admission, **options)
    assert result["result"] == {"missing_admission": "admission_missing", "not_found": "not_found",
                                  "time_not_supported": "replay_parameters_not_supported"}[failure]
    assert set(result) == ENVELOPE | {"op", "round_id", "item_id", "result"} | (
        set() if failure == "missing_admission" else {"admission_sha256"})
