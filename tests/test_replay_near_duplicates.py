"""Replay admission keeps ordinal template cards distinct without widening writes."""

from __future__ import annotations

import inspect
import json

import pytest

from piia_engram import mcp_server, write_provenance
from piia_engram.core import Engram, SIMILARITY_DUPLICATE_THRESHOLD
from piia_engram.isolated_store import GuardRefused
from piia_engram.storage import overflow_batch_scope
from test_isolated_store import ADMIT, RETIRE, _card, _snap
from test_replay_experience import ADMITTED, CLOCK, CUT, EARLY, _world


TEMPLATE = ("Demo decision point ({family} #{ordinal}): decision {decision}, "
            "side {side}; result {outcome}.")


def _template_card(ordinal):
    return _card(str(ordinal), "Q1", EARLY, summary=TEMPLATE.format(
        family="Q1", ordinal=ordinal,
        decision="retain the prepared example with complete evidence for independent checks and later review",
        side="left", outcome="complete"))


def _admit(w, card):
    args = {"admitted_before": ADMITTED, "now": CLOCK} if w.pr.mode == "replay_experience" else {}
    return w.pr.admit(card, "R1", ADMIT, **args)


def _rows(w):
    return w.pr._rows(w.pr._engram(read_only=True))


def _recall(w, **kwargs):
    args = dict(evidence_before=CUT, admitted_before=CUT, now=CLOCK, limit=600)
    args.update(kwargs)
    return w.pr.recall("dp-replay", "R2", **args)


def test_replay_template_600_cards_are_verified_and_recallable(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, limits={"soft_cap": 600, "hard_cap": 600})
    similarity = w.pr._engram(read_only=True)._bigram_similarity(
        _template_card(1)["summary"], _template_card(2)["summary"])
    assert SIMILARITY_DUPLICATE_THRESHOLD <= similarity < 1.0
    admitted = []
    for ordinal in range(1, 601):
        receipt = _admit(w, _template_card(ordinal))
        assert receipt["result"] == "admitted", (ordinal, receipt)
        admitted.append(receipt["item_id"])
    rows = _rows(w)
    assert len(rows) == 600
    assert {row["id"] for row in rows} == set(admitted)
    assert all(row["tier"] == "verified" and "duplicate_candidate" not in row for row in rows)
    assert {row["id"] for row in _recall(w)["items"]} == set(admitted)
    # Both temporal bounds are strict; the logical clock still validates cuts.
    assert _recall(w, admitted_before=ADMITTED)["items"] == []
    assert _recall(w, evidence_before=EARLY)["items"] == []
    with pytest.raises(GuardRefused, match="evidence_after_clock"):
        _recall(w, admitted_before=ADMITTED, now=ADMITTED)
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_paired_ordinal_near_duplicates_keep_production_demotion(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    first = _admit(w, _template_card(1))
    second = _admit(w, _template_card(2))
    assert first["result"] == "admitted"
    rows = {row["id"]: row for row in _rows(w)}
    row = rows[second["item_id"]]
    if mode == "production":
        assert second["result"] == "not_verified_after_write"
        assert row["tier"] == "staging" and row["approval_status"] == "pending"
        assert row["duplicate_candidate"]["existing_id"] == first["item_id"]
    else:
        assert second["result"] == "admitted"
        assert row["tier"] == "verified" and "duplicate_candidate" not in row
        assert {item["id"] for item in _recall(w)["items"]} == {first["item_id"], second["item_id"]}


def test_replay_normalized_exact_duplicate_still_refused(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, _template_card(1))
    card = _template_card(1)
    card["summary"] = "Lesson: " + "  ".join(card["summary"].upper().split())
    card["detail"] = "A different explanation for the same normalized summary."
    again = _admit(w, card)
    assert again["result"] == "duplicate" and again["item_id"] == first["item_id"]
    assert len(_rows(w)) == 1


@pytest.mark.parametrize("state,expected", [("retired", "duplicate_retired"), ("rejected", "rejected_before")])
def test_replay_retired_and_tombstoned_twins_still_refused(tmp_path, monkeypatch, state, expected):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, _template_card(1))
    if state == "retired":
        assert w.pr.retire(first["item_id"], "R2", RETIRE)["result"] == "retired"
    else:
        assert w.pr.owner_reject(first["item_id"], "Owner")["result"] == "tombstoned"
    assert _admit(w, _template_card(1))["result"] == expected
    assert len(_rows(w)) == 1


def test_replay_queued_archived_twin_rule_is_unchanged(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    row = {"id": "L-archived", "summary": _template_card(1)["summary"], "tier": "staging"}
    archive = eng._knowledge_dir / "overflow_archive" / "lessons.jsonl"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = eng.add_lesson({"summary": row["summary"], "tier": "staging"}, _replay_admission=True)
    assert result["status"] == "duplicate" and result["existing_id"] == row["id"]
    assert _rows(w) == []


def test_replay_batch_archived_twin_rule_is_unchanged(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    row = {"id": "L-batch", "summary": _template_card(1)["summary"]}
    with overflow_batch_scope() as batch:
        batch["archived_rows"]["lesson"].append(row)
        result = eng.add_lesson({"summary": row["summary"], "tier": "verified"}, _replay_admission=True)
    assert result["status"] == "duplicate" and result["existing_id"] == row["id"]
    assert _rows(w) == []


def test_replay_core_default_still_demotes_near_duplicates(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    eng.add_lesson({"summary": _template_card(1)["summary"], "tier": "verified"})
    second = eng.add_lesson({"summary": _template_card(2)["summary"], "tier": "verified"})
    assert second["tier"] == "staging" and second["duplicate_candidate"]


@pytest.mark.parametrize("mode,origin", [("production", write_provenance.ORIGIN_LOCAL),
                                       ("production", write_provenance.ORIGIN_MCP),
                                       ("replay_experience", write_provenance.ORIGIN_MCP)])
def test_replay_admission_option_refused_for_production_or_mcp(tmp_path, monkeypatch, mode, origin):
    w = _world(tmp_path, monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    before = _snap(w.pr.root)
    with write_provenance.origin_scope(origin):
        with pytest.raises(GuardRefused, match="replay_parameters_not_supported"):
            eng.add_lesson({"summary": _template_card(1)["summary"]}, _replay_admission=True)
    assert _snap(w.pr.root) == before


def test_mcp_payload_cannot_enable_replay_admission(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    eng.add_lesson({"summary": _template_card(1)["summary"], "tier": "verified"})
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        second = eng.add_lesson({"summary": _template_card(2)["summary"], "tier": "verified",
                                 "_replay_admission": True})
    assert second["tier"] == "staging" and second["duplicate_candidate"]
    assert "_replay_admission" not in inspect.signature(mcp_server.add_lesson).parameters


def test_removing_replay_admission_option_restores_demotion(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    original = Engram.add_lesson

    def without_option(self, *args, **kwargs):
        kwargs.pop("_replay_admission", None)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Engram, "add_lesson", without_option)
    assert _admit(w, _template_card(1))["result"] == "admitted"
    assert _admit(w, _template_card(2))["result"] == "not_verified_after_write"
