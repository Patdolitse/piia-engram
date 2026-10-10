"""Generic replay experience boundaries and paired chronological recall checks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import Config, GuardRefused, IsolatedStore, init_root
from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import ADMIT, _card, _make_world, _snap

MODE = "replay_experience"
EARLY = "2020-01-01T00:00:00Z"
ADMITTED = "2020-01-02T00:00:00Z"
CUT = "2020-01-03T00:00:00Z"
CLOCK = "2020-01-04T00:00:00Z"


def _world(tmp_path, monkeypatch, mode=MODE, limits=None):
    w = _make_world(tmp_path, monkeypatch, init=False, limits=limits)
    w.data["mode"] = mode
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    w.cfg = Config.load(w.cfg_path)
    for key, value in build_child_env({}, w.cfg).items():
        monkeypatch.setenv(key, value)
    w.pr = init_root(w.cfg)
    for point in ("dp-replay", "dp-test", "dp-live"):
        path = w.dps / f"{point}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["as_of_utc"] = CUT
        path.write_text(json.dumps(data), encoding="utf-8")
    return w


def _admit(w, subject="1", family="Q1", evidence=EARLY, **kwargs):
    return w.pr.admit(_card(subject, family, evidence), "R1", ADMIT,
                      admitted_before=ADMITTED, now=CLOCK, **kwargs)


def _recall(w, **kwargs):
    options = dict(evidence_before=CUT, admitted_before=CUT, now=CLOCK, query="cache")
    options.update(kwargs)
    return w.pr.recall("dp-replay", "R2", **options)


@pytest.mark.parametrize("point", ["dp-replay", "dp-test"])
def test_paired_same_query_excludes_normal_and_returns_replay(tmp_path, monkeypatch, point):
    normal = _world(tmp_path / "normal", monkeypatch, mode="production")
    result = normal.pr.admit(_card("1", "Q1", EARLY), "R1", ADMIT)
    assert result["result"] == "admitted"
    recalled = normal.pr.recall(point, "R2", evidence_before=CUT,
                                admitted_before="2099-01-01T00:00:00Z", query="cache")
    assert recalled["items"] == []
    assert recalled["excluded"] == {"family_excluded": 1}
    replay = _world(tmp_path / "replay", monkeypatch)
    assert _admit(replay)["result"] == "admitted"
    recalled = replay.pr.recall(point, "R2", evidence_before=CUT,
                                admitted_before=CUT, now=CLOCK, query="cache")
    assert [row["subject_id"] for row in recalled["items"]] == ["1"]


def test_admitted_before_injection_uses_logical_time_not_receipt_time(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    receipt = _admit(w)
    assert receipt["result"] == "admitted"
    assert datetime.fromisoformat(receipt["ts"].replace("Z", "+00:00")) > datetime(2020, 1, 4, tzinfo=timezone.utc)
    assert [row["id"] for row in _recall(w)["items"]] == [receipt["item_id"]]
    assert _recall(w, admitted_before=ADMITTED)["items"] == []


def test_clock_injection_refuses_evidence_after_caller_clock(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    receipt = _admit(w, evidence="2020-01-05T00:00:00Z")
    assert receipt["result"] == "evidence_after_clock"
    assert w.pr._rows(w.pr._engram(read_only=True)) == []
    assert _admit(w, evidence=CLOCK)["result"] == "admitted"


@pytest.mark.parametrize("field,value,code", [
    ("admitted_before", None, "admitted_before_required"),
    ("admitted_before", "2020-01-02T00:00:00", "admitted_before_invalid"),
    ("admitted_before", "invalid", "admitted_before_invalid"),
    ("admitted_before", "2099-01-01T00:00:00Z", "admitted_before_future"),
    ("now", None, "clock_required"),
    ("now", "2020-01-04T00:00:00", "clock_invalid"),
    ("now", "invalid", "clock_invalid"),
])
def test_replay_admission_requires_valid_injections(tmp_path, monkeypatch, field, value, code):
    w = _world(tmp_path, monkeypatch)
    args = dict(admitted_before=ADMITTED, now=CLOCK)
    args[field] = value
    receipt = w.pr.admit(_card("1", "Q1", EARLY), "R1", ADMIT, **args)
    assert receipt["result"] == code
    assert w.pr.receipts()[-1]["result"] == code
    assert w.pr._rows(w.pr._engram(read_only=True)) == []


def test_timezone_offsets_and_small_future_skew(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    receipt = w.pr.admit(_card("1", "Q1", EARLY), "R1", ADMIT,
                          admitted_before="2020-01-02T08:00:00+08:00", now=CLOCK)
    assert receipt["result"] == "admitted"
    assert receipt["admitted_before"] == "2020-01-02T00:00:00.000000Z"
    small_future = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    assert w.pr.admit(_card("2", "Q1", EARLY), "R1", ADMIT,
                      admitted_before=small_future, now=small_future)["result"] == "admitted"


def test_replay_keeps_as_of_truncation_and_explicit_family_exclusion(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    assert _admit(w)["result"] == "admitted"
    assert _admit(w, subject="2", evidence=CUT)["result"] == "admitted"
    result = _recall(w, evidence_before="2099-01-01T00:00:00Z")
    assert [row["subject_id"] for row in result["items"]] == ["1"]
    assert result["excluded"] == {"evidence_after": 1}
    assert _recall(w, extra_exclude_families=["q1"])["items"] == []


def test_root_mode_is_pinned_in_metadata_and_receipt_ledger(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    assert json.loads((w.pr.root / "isolated_store_root.json").read_text())["mode"] == MODE
    assert w.pr.receipts()[0]["store_mode"] == MODE
    assert IsolatedStore.open(w.cfg).mode == MODE


@pytest.mark.parametrize("target", ["config", "metadata", "property"])
def test_mode_change_is_refused_and_audited(tmp_path, monkeypatch, target):
    w = _world(tmp_path, monkeypatch)
    with pytest.raises(GuardRefused) as exc:
        if target == "config":
            w.cfg.mode = "production"
            IsolatedStore.open(w.cfg)
        elif target == "metadata":
            path = w.pr.root / "isolated_store_root.json"
            data = json.loads(path.read_text())
            data["mode"] = "production"
            path.write_text(json.dumps(data), encoding="utf-8")
            IsolatedStore.open(w.cfg)
        else:
            w.pr.mode = "production"
    assert exc.value.code in {"guard_mode_immutable", "guard_replay_experience_root"}
    assert exc.value.code in (w.pr.receipts_dir / "refusals.jsonl").read_text()


@pytest.mark.parametrize("read_only", [False, True])
def test_production_engram_refuses_replay_root_before_any_write(tmp_path, monkeypatch, read_only):
    w = _world(tmp_path, monkeypatch)
    before = _snap(w.pr.root)
    with pytest.raises(GuardRefused) as exc:
        Engram(root=w.pr.root, read_only=read_only)
    assert exc.value.code == "guard_replay_experience_root"
    assert _snap(w.pr.root) == before


def test_production_isolated_config_refuses_replay_root(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    data = {**w.data, "mode": "production"}
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(Config(data))
    assert exc.value.code == "guard_replay_experience_root"


@pytest.mark.parametrize("placement", ["bundle", "lesson", "archive", "nested"])
@pytest.mark.parametrize("merge,dry_run", [(True, False), (False, False), (True, True)])
def test_normal_import_refuses_marked_material_before_changes(tmp_path, placement, merge, dry_run):
    normal = Engram(root=tmp_path / "normal")
    payload = {"schema_version": "4.0", "identity": {}, "knowledge": {}, "projects": {}}
    row = {"id": "lesson-replay", "summary": "generic replay observation", "store_mode": MODE}
    if placement == "bundle":
        payload["store_mode"] = MODE
    elif placement == "lesson":
        payload["knowledge"]["lessons"] = [row]
    elif placement == "archive":
        payload["overflow_archive"] = {"lessons": [row]}
    else:
        payload["knowledge"]["lessons"] = [{"summary": "observation", "provenance": {"store_mode": MODE}}]
    path = tmp_path / "marked.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = _snap(normal.root)
    result = normal.import_all(str(path), merge=merge, dry_run=dry_run, allow_over_cap=True)
    assert result["error"] == "replay_experience_import_refused"
    assert result["changed"] is False
    assert _snap(normal.root) == before


@pytest.mark.parametrize("exclude_pending", [False, True])
def test_exports_and_entry_roundtrips_keep_mode_marker(tmp_path, monkeypatch, exclude_pending):
    w = _world(tmp_path / "source", monkeypatch)
    assert _admit(w)["result"] == "admitted"
    path = w.pr._engram(read_only=False).export_all(str(tmp_path / "out.json"), exclude_pending=exclude_pending)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["store_mode"] == MODE
    assert payload["knowledge"]["lessons"][0]["store_mode"] == MODE
    target = _world(tmp_path / "target", monkeypatch)
    handle = target.pr._engram(read_only=False)
    assert not handle.import_all(path).get("error")
    second = json.loads(Path(handle.export_all(str(tmp_path / "out2.json"))).read_text(encoding="utf-8"))
    assert second["store_mode"] == MODE
    assert second["knowledge"]["lessons"][0]["store_mode"] == MODE
    normal = Engram(root=tmp_path / "normal")
    assert normal.import_all(str(tmp_path / "out2.json"))["error"] == "replay_experience_import_refused"


def test_replay_near_duplicate_gate_still_applies(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    assert _admit(w)["result"] == "admitted"
    assert _admit(w)["result"] == "duplicate"
    assert len(w.pr._rows(w.pr._engram(read_only=True))) == 1


@pytest.mark.parametrize("mode,cap", [("production", 1001), (MODE, 10001), (MODE, True), (MODE, 1400.5)])
def test_capacity_upper_bound_and_type_validation(tmp_path, monkeypatch, mode, cap):
    with pytest.raises(GuardRefused) as exc:
        _world(tmp_path, monkeypatch, mode=mode, limits={"soft_cap": 1, "hard_cap": cap})
    assert exc.value.code == "guard_limits_invalid"


def test_replay_capacity_holds_1400_real_cards_and_refuses_1401(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, limits={"soft_cap": 1400, "hard_cap": 1400})
    for index in range(1400):
        receipt = _admit(w, subject=str(index))
        assert receipt["result"] == "admitted", (index, receipt)
    rows = w.pr._rows(w.pr._engram(read_only=True))
    assert len(rows) == 1400
    assert all(row["tier"] == "verified" for row in rows)
    assert _admit(w, subject="overflow")["result"] == "capacity_full"
    assert len(_recall(w, limit=1400)["items"]) == 1400
    assert w.pr.reconcile()["problems"] == []
