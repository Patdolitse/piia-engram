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
    normal = _world(tmp_path / "normal", monkeypatch, mode="production")
    assert normal.pr.admit(_card("1", "Q2", EARLY), "R1", ADMIT)["result"] == "admitted"
    assert normal.pr.recall("dp-replay", "R2", evidence_before=CUT, admitted_before=CUT)["items"] == []
    w = _world(tmp_path / "replay", monkeypatch)
    receipt = _admit(w, family="Q2")
    assert receipt["result"] == "admitted"
    assert datetime.fromisoformat(receipt["ts"].replace("Z", "+00:00")) > datetime(2020, 1, 4, tzinfo=timezone.utc)
    assert [row["id"] for row in _recall(w)["items"]] == [receipt["item_id"]]
    assert _recall(w, admitted_before=ADMITTED)["items"] == []


def test_clock_injection_refuses_evidence_after_caller_clock(tmp_path, monkeypatch):
    normal = _world(tmp_path / "normal", monkeypatch, mode="production")
    assert normal.pr.admit(_card("1", "Q1", "2020-01-05T00:00:00Z"), "R1", ADMIT)["result"] == "admitted"
    w = _world(tmp_path / "replay", monkeypatch)
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
    assert json.loads((w.pr.root / "isolated_store_root.json").read_text(encoding="utf-8"))["mode"] == MODE
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
            data = json.loads(path.read_text(encoding="utf-8"))
            data["mode"] = "production"
            path.write_text(json.dumps(data), encoding="utf-8")
            IsolatedStore.open(w.cfg)
        else:
            w.pr.mode = "production"
    assert exc.value.code in {"guard_mode_immutable", "guard_replay_experience_root"}
    assert exc.value.code in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


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


def test_replay_near_duplicate_candidate_keeps_existing_gate(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    assert _admit(w)["result"] == "admitted"
    similar = _card("1", "Q1", EARLY)
    similar["summary"] += " revised"
    result = w.pr.admit(similar, "R1", ADMIT, admitted_before=ADMITTED, now=CLOCK)
    assert result["result"] != "admitted"
    assert len(_recall(w)["items"]) == 1


def test_normal_direct_entry_ingest_refuses_marker(tmp_path):
    normal = Engram(root=tmp_path / "normal")
    result = normal.add_lesson({"summary": "generic replay observation", "store_mode": MODE})
    assert result["error"] == "replay_experience_import_refused"
    assert normal.get_lessons() == []


@pytest.mark.parametrize("mode", ["unknown", None, [], True])
def test_invalid_root_mode_is_refused(tmp_path, monkeypatch, mode):
    with pytest.raises(GuardRefused) as exc:
        _world(tmp_path, monkeypatch, mode=mode)
    assert exc.value.code == "guard_mode_invalid"


def test_opened_handle_refuses_metadata_change_on_recall_and_rebind(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    path = w.pr.root / "isolated_store_root.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mode"] = "production"
    path.write_text(json.dumps(data), encoding="utf-8")
    for action in (lambda: _recall(w), lambda: w.pr.owner_rebind("Owner")):
        with pytest.raises(GuardRefused) as exc:
            action()
        assert exc.value.code == "guard_mode_immutable"
    assert "guard_mode_immutable" in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


def test_legacy_normal_root_defaults_to_production(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    path = w.pr.root / "isolated_store_root.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("mode")
    path.write_text(json.dumps(data), encoding="utf-8")
    records = w.pr.receipts()
    records[0].pop("store_mode")
    w.pr.receipts_path.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
    assert IsolatedStore.open(w.cfg).mode == "production"
    assert Engram(root=w.pr.root, read_only=True)._store_mode == "production"


def test_replay_init_ledger_prevents_metadata_mode_replacement(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    path = w.pr.root / "isolated_store_root.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mode"] = "production"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(Config({**w.data, "mode": "production"}))
    assert exc.value.code == "guard_mode_immutable"


@pytest.mark.parametrize("args,code", [
    ({"now": None}, "clock_required"),
    ({"now": "2020-01-04T00:00:00"}, "clock_invalid"),
    ({"admitted_before": "2099-01-01T00:00:00Z"}, "admitted_before_future"),
    ({"admitted_before": None}, "admitted_before_required"),
    ({"now": ADMITTED}, "admitted_before_after_clock"),
])
def test_recall_injection_validation_is_audited(tmp_path, monkeypatch, args, code):
    w = _world(tmp_path, monkeypatch)
    with pytest.raises(GuardRefused) as exc:
        _recall(w, **args)
    assert exc.value.code == code
    assert code in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


def test_recall_evidence_order_uses_injected_clock(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    with pytest.raises(GuardRefused) as exc:
        _recall(w, now=ADMITTED, admitted_before=ADMITTED)
    assert exc.value.code == "evidence_after_clock"


def test_production_refuses_replay_only_admission_parameters(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    assert _admit(w)["result"] == "replay_parameters_not_supported"
    assert w.pr._rows(w.pr._engram(read_only=True)) == []


def test_text_exports_keep_marker_and_normal_text_import_refuses_it(tmp_path, monkeypatch):
    from piia_engram.agents_md_export import build_agents_md_export
    from piia_engram.compat import export_to_openclaw, import_from_openclaw, preview_openclaw
    from piia_engram.isolated_store import REPLAY_EXPORT_MARKER

    w = _world(tmp_path / "replay", monkeypatch)
    assert _admit(w)["result"] == "admitted"
    eng = w.pr._engram(read_only=False)
    assert REPLAY_EXPORT_MARKER in eng.export_identity_card()
    assert REPLAY_EXPORT_MARKER in eng.export_knowledge_report()
    assert REPLAY_EXPORT_MARKER in eng.export_review_page().read_text(encoding="utf-8")
    assert REPLAY_EXPORT_MARKER in build_agents_md_export(lessons=eng.get_lessons())
    exported = export_to_openclaw(eng, str(tmp_path / "text-export"))
    assert all(REPLAY_EXPORT_MARKER in Path(path).read_text(encoding="utf-8") for path in exported["files"])
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    normal = Engram(root=tmp_path / "normal")
    memory = str(tmp_path / "text-export" / "MEMORY.md")
    before = _snap(normal.root)
    for operation in (preview_openclaw, import_from_openclaw):
        result = operation(normal, memory_path=memory)
        assert result["error"] == "replay_experience_import_refused"
        assert result["changed"] is False
    assert _snap(normal.root) == before


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
