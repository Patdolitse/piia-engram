"""Synthetic historical lifecycle decisions retain both clocks."""

import pytest

from piia_engram.isolated_store import IsolatedStore, parse_utc_z
from test_isolated_events import _admit
from test_isolated_store import RESTORE, RETIRE, _make_world
from test_replay_experience import ADMITTED, CLOCK, CUT, _recall, _world

RETIRED = "2020-01-02T12:00:00Z"
RESTORED = "2020-01-02T18:00:00Z"


def test_logical_retire_and_restore_are_visible_at_historical_points(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    retired = w.pr.retire(first["item_id"], "round", RETIRE, effective_at=RETIRED, now=CLOCK)
    assert retired["result"] == "retired"
    assert retired["effective_at"] == "2020-01-02T12:00:00.000000Z"
    assert parse_utc_z(retired["ts"], "ts") > parse_utc_z(CLOCK, "clock")
    assert _recall(w, admitted_before=RETIRED)["items"]  # strict upper cut
    assert _recall(w, admitted_before="2020-01-02T15:00:00Z")["items"] == []
    restored = w.pr.restore(first["item_id"], "round", RESTORE, effective_at=RESTORED, now=CLOCK)
    assert restored["result"] == "restored"
    assert restored == w.pr.receipts()[-1]
    assert restored["clock"] == "2020-01-04T00:00:00.000000Z"
    assert _recall(w, admitted_before=RESTORED)["items"] == []
    assert [r["id"] for r in _recall(w)["items"]] == [first["item_id"]]
    w.pr.owner_retire(first["item_id"], "Owner")
    assert _recall(w, admitted_before=RETIRED)["items"] == []


@pytest.mark.parametrize("op,verdict", [("retire", RETIRE), ("restore", RESTORE)])
@pytest.mark.parametrize("args,code", [
    ({"effective_at": "2020-01-02T12:00:00" , "now": CLOCK}, "effective_at_invalid"),
    ({"effective_at": "invalid", "now": CLOCK}, "effective_at_invalid"),
    ({"effective_at": "", "now": CLOCK}, "effective_at_required"),
    ({"effective_at": RETIRED}, "clock_required"),
    ({"effective_at": RETIRED, "now": "invalid"}, "clock_invalid"),
    ({"effective_at": "2099-01-01T00:00:00Z", "now": CLOCK}, "effective_at_future"),
    ({"effective_at": "2020-01-05T00:00:00Z", "now": CLOCK}, "effective_at_after_clock"),
    ({"effective_at": "2020-01-01T00:00:00Z", "now": CLOCK}, "effective_at_before_admission"),
    ({"now": CLOCK}, "effective_at_required"),
])
def test_logical_lifecycle_validation_changes_no_rows(tmp_path, monkeypatch, op, verdict, args, code):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    before = w.pr.state_hash()
    result = getattr(w.pr, op)(first["item_id"], "round", verdict, **args)
    assert result["result"] == code
    assert w.pr.state_hash() == before


@pytest.mark.parametrize("op,verdict", [("retire", RETIRE), ("restore", RESTORE)])
def test_production_refuses_logical_lifecycle_parameters(tmp_path, monkeypatch, op, verdict):
    w = _make_world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    before = w.pr.state_hash()
    result = getattr(w.pr, op)(first["item_id"], "round", verdict, effective_at=RETIRED, now=CLOCK)
    assert result["result"] == "replay_parameters_not_supported"
    assert w.pr.state_hash() == before


def test_offsets_and_admission_boundary_are_accepted(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    result = w.pr.retire(first["item_id"], "round", RETIRE,
                         effective_at="2020-01-02T08:00:00+08:00", now=CLOCK)
    assert result["effective_at"] == "2020-01-02T00:00:00.000000Z"
    assert _recall(w)["items"] == []


def test_old_lifecycle_receipts_keep_wall_clock_semantics(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, None)
    retired = w.pr.retire(first["item_id"], "round", RETIRE)
    assert "effective_at" not in retired and "clock" not in retired
    assert _recall(w)["items"]  # a wall-clock retirement happens after CUT
    original_state, _ = IsolatedStore._replay(w.pr.receipts(), parse_utc_z(CUT, "cut"), experience=True)
    assert original_state[first["item_id"]]["active"] is True


def test_backdated_logical_ops_are_ordered_by_effective_time(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    first = _admit(w, "source:1:lesson:v1")
    w.pr.retire(first["item_id"], "round", RETIRE, effective_at=RESTORED, now=CLOCK)
    w.pr.restore(first["item_id"], "round", RESTORE, effective_at=RETIRED, now=CLOCK)
    assert _recall(w)["items"] == []  # the retirement is later in logical time
