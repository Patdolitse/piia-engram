"""Body-free verification of sealed synthetic recall results."""

import json

import pytest

from piia_engram.isolated_store import IsolatedStore, _sha256_json, content_hash
from test_isolated_events import _admit
from test_isolated_store import RETIRE, _make_world, _recall


def test_recall_receipt_matches_ledger_and_binds_the_input_snapshot(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    second = _admit(w, "source:2:lesson:v1")
    before = w.pr.receipts()[-1]
    library_digest = w.pr.state_hash()
    result = _recall(w.pr, query="cache", limit=1)
    receipt = result["receipt"]
    assert receipt == w.pr.receipts()[-1]
    assert receipt["snapshot"]["ledger_seq"] == before["seq"]
    assert receipt["snapshot"]["ledger_sha256"] == _sha256_json(before)
    assert receipt["snapshot"]["library_sha256"] == library_digest
    assert receipt["snapshot_id"] == _sha256_json(receipt["snapshot"])
    assert receipt["returned_ids"] == [r["id"] for r in result["items"]]
    assert receipt["returned_content_sha256"] == [second["content_sha256"]]
    rows = {r["id"]: r for r in w.pr._rows(w.pr._engram(read_only=True))}
    assert receipt["returned_content_sha256"] == [content_hash(rows[item_id]) for item_id in receipt["returned_ids"]]
    assert receipt["effective_evidence_before"] == result["effective_evidence_before"]
    assert receipt["effective_admitted_before"] == result["effective_admitted_before"]
    assert receipt["query"] == "cache" and receipt["limit"] == 1
    assert receipt["root_mode"] == "production"
    assert receipt["as_of_utc"] == "2099-01-01T00:00:00Z"
    assert receipt["omitted"] == {"limit": 1}
    assert w.pr.verify_recall_receipt(receipt)["unchanged"] is True
    # Reopening the same ledger yields the same snapshot calculation.
    reopened = IsolatedStore.open(w.cfg)
    assert reopened.verify_recall_receipt(json.loads(json.dumps(receipt))) == w.pr.verify_recall_receipt(receipt)
    later = _recall(w.pr, query="cache", limit=1)["receipt"]
    assert later["snapshot_id"] != receipt["snapshot_id"]  # recall is itself a ledger op
    assert later["returned_ids"] == receipt["returned_ids"]
    assert first["item_id"] != second["item_id"]


@pytest.mark.parametrize("change,reason", [("retire", "retired"), ("owner_retire", "vetoed"),
                                         ("owner_reject", "vetoed"), ("content", "content_changed"),
                                         ("missing", "missing")])
def test_verification_reports_exact_item_changes_without_bodies(tmp_path, monkeypatch, change, reason):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    sealed = _recall(w.pr)["receipt"]
    if change == "retire":
        w.pr.retire(first["item_id"], "round", RETIRE)
    elif change.startswith("owner_"):
        getattr(w.pr, change)(first["item_id"], "Owner")
    else:
        path = w.pr.root / "knowledge" / "lessons.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        if change == "content":
            rows[0]["detail"] += " altered"
        else:
            rows = []
        path.write_text(json.dumps(rows), encoding="utf-8")
    verified = w.pr.verify_recall_receipt(sealed)
    assert verified["unchanged"] is False
    assert verified["changes"] == [{"id": first["item_id"], "reasons": [reason]}]
    assert "summary" not in json.dumps(verified) and "detail" not in json.dumps(verified)
    assert sealed["returned_ids"] == [first["item_id"]]


def test_omission_counts_cover_filters_and_limits_without_changing_legacy_excluded(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    _admit(w, "source:1:lesson:v1")
    result = _recall(w.pr, subject_ids=["other"])
    assert result["excluded"] == {}
    assert result["receipt"]["omitted"] == {"subject_excluded": 1}
    assert _recall(w.pr, query=" ")["receipt"]["omitted"] == {"empty_query": 1}
    assert _recall(w.pr, query="unmatched")["receipt"]["omitted"] == {"query_no_match": 1}
    assert _recall(w.pr, limit=0)["receipt"]["omitted"] == {"limit": 1}
    assert _recall(w.pr, admitted_before="2020-01-01T00:00:00Z")["receipt"]["omitted"] == {"admission_after": 1}


def test_verification_refuses_modified_or_foreign_receipt(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    _admit(w, "source:1:lesson:v1")
    receipt = _recall(w.pr)["receipt"]
    modified = dict(receipt, returned_content_sha256=["0" * 64])
    assert w.pr.verify_recall_receipt(modified)["result"] == "recall_receipt_invalid"
    other = _make_world(tmp_path / "other", monkeypatch)
    assert other.pr.verify_recall_receipt(receipt)["result"] == "recall_receipt_invalid"


def test_empty_recall_has_a_verifiable_receipt_without_mutating_root(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch)
    before = w.pr.state_hash()
    result = _recall(w.pr)
    assert result["items"] == []
    assert result["receipt"]["returned_content_sha256"] == []
    assert w.pr.state_hash() == before
    assert w.pr.verify_recall_receipt(result["receipt"])["unchanged"]
