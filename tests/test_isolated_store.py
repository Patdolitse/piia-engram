"""Isolated, admission-gated memory store: guards, valve, receipts, recall, veto, proofs.

Every test works in tmp_path with a FAKE Owner home and store; the real Owner store
is never resolved, read or written (pin_deny_list gets owner_paths=()). Each run
witness is paired with a reverse check: with the protection switched off, the harm
it prevents actually happens, so the witness is known to be sensitive.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram import isolated_store as iso_mod
from piia_engram.isolated_store import (
    Config,
    GuardRefused,
    IsolatedStore,
    ReceiptsUnreadable,
    RecallRefused,
    content_hash,
    init_root,
    utc_now_z,
)
from piia_engram.isolated_store_launch import build_child_env, pin_deny_list

SRC = str(Path(__file__).resolve().parents[1] / "src")
ADMIT = {"verdict": "admit", "judge_version": "j1", "decision_record_id": "rec-1"}
LATER = "2099-01-01T00:00:00Z"
HISTORICAL = "2026-09-01T00:00:00Z"  # a replayed / test decision point's own time
RETIRE = {"verdict": "retire", "judge_version": "j1", "decision_record_id": "rec-2"}
RESTORE = {"verdict": "restore", "judge_version": "j1", "decision_record_id": "rec-3"}
# A key-shaped string assembled at runtime so the repository scanner never sees a literal.
_FAKE_KEY = "sk-" + "ant-api03-" + "A" * 40


def _snap(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


def _make_world(tmp_path: Path, monkeypatch, *, limits: dict | None = None, init: bool = True):
    owner_home = tmp_path / "owner_home"
    owner_store = owner_home / "OwnerData" / ".engram"
    (owner_store / "knowledge").mkdir(parents=True)
    (owner_store / "knowledge" / "lessons.json").write_text("[]", encoding="utf-8")
    (owner_store / "approval_mode.json").write_text("{}", encoding="utf-8")
    mem = owner_home / ".claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True)
    (mem / "m.md").write_text("an owner memory that must never be imported", encoding="utf-8")
    area = tmp_path / "caller_area"
    deny = area / "deny.json"
    sha = pin_deny_list(deny, owner_home=str(owner_home), owner_engram_dir=str(owner_store),
                        extra=[str(owner_home / "OwnerData")])
    dps = area / "decision_points"
    dps.mkdir(parents=True)
    for dp, mode, fam, as_of in (("dp-live", "live", "Q2", LATER), ("dp-replay", "replay", "Q1", HISTORICAL),
                                 ("dp-test", "test", "Q1", HISTORICAL)):
        (dps / f"{dp}.json").write_text(json.dumps({"kind": "D_VERDICT", "mode": mode, "family_code": fam,
                                                    "as_of_utc": as_of}), encoding="utf-8")
    data = {"root": str(area / "root"), "receipts_dir": str(area / "receipts"), "fake_home": str(area / "home"),
            "cache_dir": str(area / "cache"), "decision_points_dir": str(dps), "deny_list_file": str(deny),
            "deny_list_sha256": sha}
    if limits:
        data["limits"] = limits
    cfg_path = area / "config.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    cfg = Config.load(cfg_path)
    for key in list(os.environ):
        if key.upper().startswith("ENGRAM_"):
            monkeypatch.delenv(key, raising=False)
    child = build_child_env(dict(os.environ), cfg)
    for key in iso_mod.FOREIGN_PATH_VARS:  # the launcher drops them; so does the in-process setup
        if key not in child:
            monkeypatch.delenv(key, raising=False)
    for key, value in child.items():
        monkeypatch.setenv(key, value)
    (area / "home").mkdir(parents=True, exist_ok=True)
    pr = init_root(cfg) if init else None
    return SimpleNamespace(tmp=tmp_path, owner_home=owner_home, owner_store=owner_store, area=area, cfg=cfg,
                           cfg_path=cfg_path, data=data, pr=pr, dps=dps)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return _make_world(tmp_path, monkeypatch)


def _card(subject: str, family: str, evidence: str, summary: str | None = None) -> dict:
    return {"summary": summary or f"S-{subject}: a distinct claim number {subject} about cache warm-up",
            "detail": f"kept because it changes the call ({subject})", "evidence_as_of": evidence,
            "source_family": family, "source_decision_point": f"dp-src-{subject}", "subject_id": subject}


def _recall(pr, dp="dp-live", *, evidence_before=LATER, admitted_before=LATER, **kw):
    return pr.recall(dp, "R4", evidence_before=evidence_before, admitted_before=admitted_before, **kw)


def _subjects(result) -> list[str]:
    return sorted(i["subject_id"] for i in result["items"])


def _rewrite_deny(world, entries: list[str]) -> None:
    raw = json.dumps({"deny": entries}).encode("utf-8")
    Path(world.data["deny_list_file"]).write_bytes(raw)
    world.data["deny_list_sha256"] = hashlib.sha256(raw).hexdigest()
    world.cfg_path.write_text(json.dumps(world.data), encoding="utf-8")
    world.cfg = Config.load(world.cfg_path)


# ---------------------------------------------------------------------------
# 1. guards (each with its reverse)
# ---------------------------------------------------------------------------


def test_root_pointing_at_the_owner_store_is_refused_and_nothing_is_written(world, monkeypatch):
    before = _snap(world.owner_home)
    world.data["root"] = str(world.owner_store)
    world.cfg_path.write_text(json.dumps(world.data), encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", str(world.owner_store))
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(Config.load(world.cfg_path))
    assert exc.value.code == "guard_owner_store"
    assert _snap(world.owner_home) == before


@pytest.mark.parametrize("target", ["store", "inside", "parent"])
def test_root_overlapping_the_owner_store_any_way_is_refused(world, monkeypatch, target):
    path = {"store": world.owner_store, "inside": world.owner_store / "knowledge",
            "parent": world.owner_home / "OwnerData"}[target]
    with pytest.raises(GuardRefused) as exc:
        iso_mod.check_candidate(path, world.cfg.deny_list(), label="root")
    assert exc.value.code == "guard_owner_store"


def test_reverse_without_the_owner_path_in_the_deny_list_the_overlap_passes(world):
    _rewrite_deny(world, [str(world.tmp / "unrelated")])
    assert iso_mod.check_candidate(world.owner_store, world.cfg.deny_list(), label="root")


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths are a Windows property")
def test_case_variants_of_the_owner_path_are_refused(world):
    variant = Path(str(world.owner_store).upper())
    with pytest.raises(GuardRefused):
        iso_mod.check_candidate(variant, world.cfg.deny_list(), label="root")


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows only")
def test_a_junction_into_the_owner_store_is_refused(world):
    link = world.tmp / "innocent_looking"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(world.owner_store)], check=True,
                   capture_output=True)
    with pytest.raises(GuardRefused) as exc:
        iso_mod.check_candidate(link, world.cfg.deny_list(), label="root")
    assert exc.value.code == "guard_owner_store"


@pytest.mark.parametrize("path", [r"\\server\share\area", r"\\?\C:\area", r"\\.\C:\area", "//server/share"])
def test_unc_and_device_paths_are_refused(world, path):
    with pytest.raises(GuardRefused) as exc:
        iso_mod.check_candidate(Path(path), [], label="root")
    assert exc.value.code == "guard_unc_or_device"


def test_engram_dir_not_the_root_is_refused(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(world.tmp / "elsewhere"))
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_engram_dir_mismatch"
    monkeypatch.setenv("ENGRAM_DIR", world.data["root"])  # reverse: the same root opens
    assert IsolatedStore.open(world.cfg)


@pytest.mark.parametrize("var,value", [("ENGRAM_APPROVAL", "strict"), ("ENGRAM_SECRET", "x"),
                                       ("ENGRAM_TEST", "1")])
def test_an_inherited_engram_variable_outside_the_allow_list_is_refused(world, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_env_not_allowed"


def test_launcher_env_drops_the_owner_shell_engram_variables(world):
    parent = {"ENGRAM_DIR": str(world.owner_store), "ENGRAM_APPROVAL": "strict", "ENGRAM_REVIEW_QUEUE_MAX": "30",
              "ENGRAM_REVIEW_QUEUE_CEILING": "30", "USERPROFILE": str(world.owner_home), "PATH": "x"}
    env = build_child_env(parent, world.cfg)
    assert "ENGRAM_APPROVAL" not in env
    assert env["ENGRAM_DIR"] == world.data["root"]
    assert env["ENGRAM_REVIEW_QUEUE_MAX"] == "1" and env["ENGRAM_RETIRED_MAX"] == "1000"
    assert env["USERPROFILE"] == world.data["fake_home"] and env["PATH"] == "x"


def test_home_not_redirected_is_refused(world, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(world.owner_home))
    monkeypatch.setenv("HOME", str(world.owner_home))
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_home_not_redirected"


def test_a_tampered_deny_list_is_refused(world):
    Path(world.data["deny_list_file"]).write_text(json.dumps({"deny": ["C:\\nothing"]}), encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "deny_list_hash_mismatch"


def test_receipts_inside_the_root_are_refused(world):
    world.data["receipts_dir"] = str(Path(world.data["root"]) / "receipts")
    world.cfg_path.write_text(json.dumps(world.data), encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(Config.load(world.cfg_path))
    assert exc.value.code == "guard_receipts_in_root"


def test_a_strict_latch_in_the_isolated_store_is_refused_and_can_be_cleared(world):
    latch = Path(world.data["root"]) / "approval_mode.json"
    latch.write_text("{}", encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "ISOLATED_STORE_STRICT_LATCHED"
    receipt = IsolatedStore.open(world.cfg, allow_strict_latch=True).owner_clear_latch("Owner")
    assert receipt["result"] == "cleared" and not latch.exists()
    assert IsolatedStore.open(world.cfg)


def test_a_moved_root_is_refused_until_the_owner_rebinds(world):
    marker_path = Path(world.data["root"]) / "isolated_store_root.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["file_id"] = marker["file_id"] + 1
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_marker_binding"
    IsolatedStore.open(world.cfg, allow_rebind=True).owner_rebind("Owner")
    assert IsolatedStore.open(world.cfg)
    assert world.pr.receipts()[-1]["op"] == "rebind"


def test_a_missing_marker_is_refused(world):
    (Path(world.data["root"]) / "isolated_store_root.json").unlink()
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_marker_missing"


def test_init_refuses_a_non_empty_directory_and_legacy_names(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, init=False)
    root = Path(w.data["root"])
    root.mkdir(parents=True)
    (root / "something.txt").write_text("x", encoding="utf-8")
    with pytest.raises(GuardRefused) as exc:
        init_root(w.cfg)
    assert exc.value.code == "guard_init_not_empty"
    assert not (root / "isolated_store_root.json").exists()
    w.data["root"] = str(tmp_path / "caller_area" / ".engram")
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", w.data["root"])
    with pytest.raises(GuardRefused) as exc:
        init_root(Config.load(w.cfg_path))
    assert exc.value.code == "guard_init_legacy_name"


def test_limits_in_the_env_that_differ_from_the_pinned_file_are_refused(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_RETIRED_MAX", "100")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_limits_mismatch"


def test_invalid_pinned_limits_refuse_to_open(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, init=False,
                    limits={"review_queue_max": 5, "review_queue_ceiling": 2})  # ceiling < max: invalid
    with pytest.raises(GuardRefused) as exc:
        init_root(w.cfg)
    assert exc.value.code == "guard_limits_invalid"


def test_a_guard_refusal_is_recorded_outside_the_chain(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    with pytest.raises(GuardRefused):
        IsolatedStore.open(world.cfg)
    refusals = (Path(world.data["receipts_dir"]) / "refusals.jsonl").read_text(encoding="utf-8")
    assert "guard_env_not_allowed" in refusals
    monkeypatch.delenv("ENGRAM_APPROVAL")
    assert IsolatedStore.open(world.cfg).reconcile()["problems"] == []  # the chain is intact


# ---------------------------------------------------------------------------
# 2. the valve
# ---------------------------------------------------------------------------


def test_admit_writes_a_verified_card_and_a_chained_receipt(world):
    receipt = world.pr.admit(_card("7", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert receipt["result"] == "admitted"
    _kind, row = world.pr._engram(read_only=True)._find_item_by_id(receipt["item_id"])
    assert row["tier"] == "verified" and row["subject_id"] == "7" and row["domain"] == "type:lesson,subject:7;"
    assert receipt["content_sha256"] == content_hash(row)
    assert receipt["seq"] == world.pr.receipts()[-2]["seq"] + 1
    assert world.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("admission", [None, {}, {"verdict": "admit"}, {"verdict": "reject", "judge_version": "j",
                                                                         "decision_record_id": "r"}])
def test_admit_without_an_admission_verdict_is_refused(world, admission):
    receipt = world.pr.admit(_card("7", "Q1", "2026-08-01T00:00:00Z"), "R4", admission)
    assert receipt["result"] == "admission_missing"
    assert world.pr._rows(world.pr._engram(read_only=True)) == []


def test_times_must_be_utc_with_z(world):
    receipt = world.pr.admit(_card("7", "Q1", "2026-08-01 00:00:00"), "R4", ADMIT)
    assert receipt["result"] == "card_invalid"
    with pytest.raises(ValueError):
        _recall(world.pr, evidence_before="2026-08-01T00:00:00+08:00")


def test_capacity_precheck_refuses_before_writing(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"soft_cap": 2, "hard_cap": 2})
    for m in ("1", "2"):
        assert w.pr.admit(_card(m, "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "admitted"
    knowledge = Path(w.data["root"]) / "knowledge"
    before = _snap(knowledge)
    assert w.pr.admit(_card("3", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "capacity_full"
    assert _snap(knowledge) == before


def test_reverse_without_the_capacity_precheck_the_library_hides_the_card_in_staging(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"soft_cap": 2, "hard_cap": 2})
    for m in ("1", "2"):
        w.pr.admit(_card(m, "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    monkeypatch.setattr(IsolatedStore, "_verified_budget_full", lambda self, eng: False)
    receipt = w.pr.admit(_card("3", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert receipt["result"] == "not_verified_after_write"  # the library parked it in staging


def test_risky_content_is_refused(world):
    secret = _card("5", "Q1", "2026-08-01T00:00:00Z",
                   summary="S-5: use key " + _FAKE_KEY + " for the feed")
    assert world.pr.admit(secret, "R4", ADMIT)["result"] == "risk_refused"
    path = _card("6", "Q1", "2026-08-01T00:00:00Z", summary="S-6: the run password=hunter2 opens it")
    assert world.pr.admit(path, "R4", ADMIT)["result"] == "risk_refused"


def test_reverse_without_the_risk_checks_a_secret_is_stored_as_verified(world, monkeypatch):
    from piia_engram import hook_digest
    from piia_engram.core import Engram

    monkeypatch.setattr(hook_digest, "output_guard_item", lambda fields: (True, ""))
    monkeypatch.setattr(Engram, "_assess_memory_risk", lambda self, entry: {"risk_level": "low", "risk_flags": []})
    secret = _card("5", "Q1", "2026-08-01T00:00:00Z",
                   summary="S-5: use key " + _FAKE_KEY + " for the feed")
    assert world.pr.admit(secret, "R4", ADMIT)["result"] == "admitted"


def test_a_direct_write_around_the_valve_is_flagged_by_reconcile(world):
    eng = world.pr._engram(read_only=False)
    eng.add_lesson({"summary": "a bypass write that skipped the admission verdict", "tier": "verified"})
    problems = world.pr.reconcile()["problems"]
    assert any(p.startswith("row_without_receipt:") for p in problems)


def test_the_module_offers_no_update_or_merge():
    for name in ("update", "update_card", "merge", "edit_type"):
        assert not hasattr(IsolatedStore, name)


# ---------------------------------------------------------------------------
# 3. recall
# ---------------------------------------------------------------------------


def test_two_clocks_replay_excludes_its_family_and_future_evidence(world):
    world.pr.admit(_card("1", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)   # own family: never for a replay
    world.pr.admit(_card("2", "Q2", "2026-08-15T00:00:00Z"), "R4", ADMIT)   # other family, old evidence: yes
    world.pr.admit(_card("3", "Q2", "2026-09-15T00:00:00Z"), "R4", ADMIT)   # other family, future evidence: no
    result = _recall(world.pr, "dp-replay", evidence_before="2026-09-01T00:00:00Z", admitted_before=LATER)
    assert _subjects(result) == ["2"]
    assert result["excluded"] == {"family_excluded": 1, "evidence_after": 1}
    assert _subjects(_recall(world.pr, "dp-test", evidence_before="2026-09-01T00:00:00Z")) == ["2"]
    assert _subjects(_recall(world.pr, "dp-live", evidence_before="2026-09-01T00:00:00Z")) == ["1", "2"]


def test_reverse_without_family_exclusion_a_replay_reads_its_own_answer(world, monkeypatch):
    world.pr.admit(_card("1", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    monkeypatch.setattr(iso_mod, "MODES_EXCLUDING_OWN_FAMILY", set())
    assert _subjects(_recall(world.pr, "dp-replay", evidence_before="2026-09-01T00:00:00Z")) == ["1"]


def test_the_caller_can_add_but_not_remove_family_exclusions(world):
    world.pr.admit(_card("1", "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    world.pr.admit(_card("2", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert _subjects(_recall(world.pr, "dp-replay", extra_exclude_families=())) == ["2"]
    assert _subjects(_recall(world.pr, "dp-live", extra_exclude_families=("Q2",))) == ["1"]


def test_admitted_before_hides_cards_admitted_later(world):
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    time.sleep(0.01)
    cut = utc_now_z()
    time.sleep(0.01)
    world.pr.admit(_card("2", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert _subjects(_recall(world.pr, admitted_before=cut)) == ["1"]


@pytest.mark.parametrize("dp", [{"mode": "live"}, {"mode": "live", "family_code": "Q2"},
                                {"mode": "rerun", "family_code": "Q2", "as_of_utc": "2026-09-01T00:00:00Z"},
                                {"mode": "live", "family_code": "Q2", "as_of_utc": "2026-09-01T00:00:00"}])
def test_recall_fails_closed_on_a_bad_decision_point_file(world, dp):
    (world.dps / "dp-bad.json").write_text(json.dumps(dp), encoding="utf-8")
    with pytest.raises(RecallRefused):
        _recall(world.pr, "dp-bad")
    with pytest.raises(RecallRefused):
        _recall(world.pr, "dp-does-not-exist")


def test_a_card_whose_text_the_library_repairs_is_still_recalled(world):
    # UTF-8 read as GBK: the library repairs this text before storing it
    mojibake = "S-8: " + "发布流程测试".encode("utf-8").decode("gbk") + " requests on a cold cache time out"
    receipt = world.pr.admit(_card("8", "Q2", "2026-08-01T00:00:00Z", summary=mojibake), "R4", ADMIT)
    _kind, row = world.pr._engram(read_only=True)._find_item_by_id(receipt["item_id"])
    input_hash = iso_mod._sha256_json({k: _card("8", "Q2", "2026-08-01T00:00:00Z", summary=mojibake).get(k)
                                      for k in iso_mod.HASHED_KEYS})
    assert row["summary"] != mojibake  # the stored text differs from the input...
    assert input_hash != receipt["content_sha256"]  # ...so an input hash could never match (reverse)
    assert _subjects(_recall(world.pr)) == ["8"]


def test_a_tampered_card_is_not_returned(world, monkeypatch):
    receipt = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    lessons = Path(world.data["root"]) / "knowledge" / "lessons.json"
    rows = json.loads(lessons.read_text(encoding="utf-8"))
    rows[0]["summary"] = "S-1: a quietly edited claim"
    lessons.write_text(json.dumps(rows), encoding="utf-8")
    result = _recall(world.pr)
    assert result["items"] == [] and result["excluded"] == {"hash_mismatch": 1}
    monkeypatch.setattr(IsolatedStore, "_hash_ok", staticmethod(lambda row, r: True))  # reverse
    assert [i["id"] for i in _recall(world.pr)["items"]] == [receipt["item_id"]]


def test_retire_and_restore_follow_the_receipt_clock(world):
    receipt = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    time.sleep(0.01)
    before_retire = utc_now_z()
    time.sleep(0.01)
    assert world.pr.retire(receipt["item_id"], "R4", RETIRE)["result"] == "retired"
    assert _subjects(_recall(world.pr)) == []
    assert _subjects(_recall(world.pr, admitted_before=before_retire)) == ["1"]  # a caller retire is not a veto
    assert world.pr.restore(receipt["item_id"], "R4", RESTORE)["result"] == "restored"
    assert _subjects(_recall(world.pr)) == ["1"]
    assert world.pr.reconcile()["problems"] == []


def test_an_archived_retired_card_is_still_returned_at_an_earlier_time(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"r_max": 1})
    first = w.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    second = w.pr.admit(_card("2", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    time.sleep(0.01)
    before = utc_now_z()
    time.sleep(0.01)
    w.pr.retire(first["item_id"], "R4", RETIRE)
    w.pr.retire(second["item_id"], "R4", RETIRE)
    eng = w.pr._engram(read_only=True)
    archived = [r["id"] for r in eng._read_overflow_archive("lesson") if not eng._is_snapshot_record(r)]
    in_file = {r["id"] for r in w.pr._rows(eng)}
    assert archived and archived[0] not in in_file, "r_max=1 must move a retired card out of lessons.json"
    assert any(r["op"] == "archived" and r["item_id"] == archived[0] for r in w.pr.receipts())
    assert _subjects(_recall(w.pr, admitted_before=before)) == ["1", "2"]
    assert w.pr.reconcile()["problems"] == []
    with monkeypatch.context() as m:  # reverse: without the archive read the card is lost
        m.setattr(IsolatedStore, "_archived_raw", lambda self, eng, item_id: None)
        result = _recall(w.pr, admitted_before=before)
        assert result["excluded"] == {"missing_in_library": 1} and len(result["items"]) == 1
    assert w.pr.restore(archived[0], "R4", RESTORE)["result"] == "restored"
    restored_subject = "1" if archived[0] == first["item_id"] else "2"
    assert _subjects(_recall(w.pr)) == [restored_subject]


def test_a_lost_receipt_tail_does_not_bring_back_a_retired_card(world, monkeypatch):
    receipt = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    world.pr.retire(receipt["item_id"], "R4", RETIRE)
    lines = world.pr.receipts_path.read_text(encoding="utf-8").splitlines()
    world.pr.receipts_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # lose the retire line
    result = _recall(world.pr)
    assert result["items"] == [] and result["excluded"] == {"receipt_library_mismatch": 1}
    monkeypatch.setattr(IsolatedStore, "_library_disagrees", lambda *a, **k: False)  # reverse
    assert _subjects(_recall(world.pr)) == ["1"]


def test_the_owner_veto_hides_a_card_at_every_time_and_tombstones_it(world):
    receipt = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    time.sleep(0.01)
    before = utc_now_z()
    out = world.pr.owner_reject(receipt["item_id"], "Owner")
    assert out["result"] == "tombstoned"
    assert _subjects(_recall(world.pr)) == [] and _subjects(_recall(world.pr, admitted_before=before)) == []
    again = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert again["result"] == "rejected_before"
    assert world.pr.reconcile()["problems"] == []


def test_limit_applies_after_filtering(world):
    for m in ("1", "2", "3"):
        world.pr.admit(_card(m, "Q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)  # excluded for replays
    world.pr.admit(_card("9", "Q2", "2026-07-01T00:00:00Z"), "R4", ADMIT)
    assert _subjects(_recall(world.pr, "dp-replay", limit=1)) == ["9"]


def test_subject_ids_are_compared_exactly(world):
    world.pr.admit(_card("12", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    world.pr.admit(_card("123", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert _subjects(_recall(world.pr, subject_ids=["12"])) == ["12"]


def test_recall_leaves_the_root_unchanged_and_writes_one_receipt(world):
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    knowledge = Path(world.data["root"]) / "knowledge"
    before = _snap(knowledge)
    n = len(world.pr.receipts())
    result = _recall(world.pr)
    assert _snap(knowledge) == before
    receipts = world.pr.receipts()
    assert len(receipts) == n + 1 and receipts[-1]["returned_ids"] == [i["id"] for i in result["items"]]
    assert receipts[-1]["decision_point_id"] == "dp-live" and receipts[-1]["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# 4. upgrade receipt
# ---------------------------------------------------------------------------


def test_a_library_version_change_writes_a_version_receipt(world, monkeypatch):
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    import piia_engram

    monkeypatch.setattr(piia_engram, "__version__", "99.0.0")
    opened = IsolatedStore.open(world.cfg)
    assert opened.receipts()[-1]["op"] == "version" and opened.receipts()[-1]["to_version"] == "99.0.0"


def test_an_upgrade_with_a_changed_card_is_blocked(world, monkeypatch):
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    lessons = Path(world.data["root"]) / "knowledge" / "lessons.json"
    rows = json.loads(lessons.read_text(encoding="utf-8"))
    rows[0]["detail"] = "migrated differently"
    lessons.write_text(json.dumps(rows), encoding="utf-8")
    import piia_engram

    monkeypatch.setattr(piia_engram, "__version__", "99.0.0")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "upgrade_blocked_hash_mismatch"


# ---------------------------------------------------------------------------
# 5. through the real launcher: isolation probe, veto wrapper
# ---------------------------------------------------------------------------

_PROBE = r'''
import json, os, sys, ntpath
seen = []
io_seen = []
def _hook(event, args):
    if event in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.rename", "os.replace", "os.remove",
                 "shutil.copyfile", "os.chmod"):
        if args:
            seen.append(str(args[0]))
            io_seen.append(str(args[0]))
sys.addaudithook(_hook)
# CPython raises no audit event for stat / lstat / realpath, so they are wrapped.
# The reverse test drops nowraps.flag next to this script to prove the wraps matter.
_WRAPS = not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nowraps.flag"))
if _WRAPS:
    for name in ("stat", "lstat"):
        orig = getattr(os, name)
        def _wrap(p, *a, _o=orig, **k):
            seen.append(str(p)); return _o(p, *a, **k)
        setattr(os, name, _wrap)
if _WRAPS and sys.platform == "win32":
    import nt
    _gf = nt._getfinalpathname
    def _final(p, _o=_gf):
        seen.append(str(p)); return _o(p)
    nt._getfinalpathname = _final
    ntpath._getfinalpathname = _final
from pathlib import Path
if _WRAPS and hasattr(Path, "_accessor"):
    # Python 3.10's accessor must not bind a Python spy as an instance method.
    for name in ("stat", "lstat"):
        setattr(type(Path._accessor), name, staticmethod(getattr(os, name)))
from piia_engram.isolated_store import IsolatedStore
pr = IsolatedStore.open()
adm = {"verdict": "admit", "judge_version": "j", "decision_record_id": "r"}
RETIRE = {"verdict": "retire", "judge_version": "j", "decision_record_id": "r2"}
RESTORE = {"verdict": "restore", "judge_version": "j", "decision_record_id": "r3"}
a = pr.admit({"summary": "S-1: probe claim about cache warm-up", "detail": "d",
              "evidence_as_of": "2026-08-01T00:00:00Z", "source_family": "Q2",
              "source_decision_point": "x", "subject_id": "1"}, "R4", adm)
pr.recall("dp-live", "R4", evidence_before="2099-01-01T00:00:00Z", admitted_before="2099-01-01T00:00:00Z")
pr.retire(a["item_id"], "R4", RETIRE)
pr.restore(a["item_id"], "R4", RESTORE)
_canary_file = Path(__file__).with_name("canary.json")
if _canary_file.is_file():
    # Canary: repeat the earlier mistake inside the hooked process -- resolve the Owner's
    # paths the way pin-deny-list does. The hook must see it.
    _c = json.loads(_canary_file.read_text(encoding="utf-8"))
    from piia_engram.isolated_store_launch import pin_deny_list
    pin_deny_list(Path(_c["out"]), owner_home=_c["owner_home"], owner_engram_dir=_c["owner_store"])
print(json.dumps({"home": str(Path.home()), "seen": seen, "io_seen": io_seen,
                  "problems": pr.reconcile()["problems"],
                  "rows": len(pr._rows(pr._engram(read_only=True)))}))
'''


def _parent_env(world) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ENGRAM_")}
    env.update({"USERPROFILE": str(world.owner_home), "HOME": str(world.owner_home),
                "ENGRAM_DIR": str(world.owner_store), "ENGRAM_APPROVAL": "strict",
                "ENGRAM_REVIEW_QUEUE_MAX": "30", "ENGRAM_REVIEW_QUEUE_CEILING": "30", "ENGRAM_RETIRED_MAX": "1",
                # no empty entry: an empty PYTHONPATH segment means "the current directory"
                "PYTHONPATH": os.pathsep.join(p for p in (SRC, os.environ.get("PYTHONPATH", "")) if p),
                "PYTHONIOENCODING": "utf-8"})
    return env


def _launch(world, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = _parent_env(world)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "piia_engram.isolated_store_launch", "--config", str(world.cfg_path),
                           *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)


def _probe(world, *, canary: bool, wraps: bool = True) -> tuple[dict, list[str], list[str]]:
    """Run the hooked probe through the launcher; (report, owner paths touched, paths outside the allow-list)."""
    probe = world.area / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    flag = world.area / "nowraps.flag"
    if wraps and flag.exists():
        flag.unlink()
    elif not wraps:
        flag.write_text("x", encoding="utf-8")
    canary_file = world.area / "canary.json"
    if canary:
        canary_file.write_text(json.dumps({"owner_home": str(world.owner_home), "owner_store": str(world.owner_store),
                                           "out": str(world.area / "canary-deny.json")}), encoding="utf-8")
    elif canary_file.exists():
        canary_file.unlink()
    out = _launch(world, "run", "--", sys.executable, str(probe))
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads(out.stdout.strip().splitlines()[-1])
    owner = os.path.normcase(str(world.owner_home))
    paths = [os.path.normcase(os.path.abspath(p)) for p in report["seen"] if p and not p.isdigit()]
    io_paths = {os.path.normcase(os.path.abspath(p)) for p in report["io_seen"] if p and not p.isdigit()}
    allowed = tuple(os.path.normcase(os.path.abspath(a)) for a in (
        world.area, sys.prefix, sys.base_prefix, sys.exec_prefix, SRC, os.path.dirname(os.__file__)))
    # POSIX realpath stats structural ancestors. Exempt only those exact metadata
    # paths, never file I/O, directory listing, or mutation at an ancestor.
    metadata_ancestors = {os.path.normcase(str(p)) for p in world.area.resolve().parents}
    outside = {p for p in paths if not p.startswith(allowed)
               and (p not in metadata_ancestors or p in io_paths)}
    return report, [p for p in paths if p.startswith(owner)], sorted(outside)


def test_isolation_probe_through_the_launcher_touches_nothing_of_the_owner(world):
    before = _snap(world.owner_home)
    report, owner_touched, outside = _probe(world, canary=False)
    assert Path(report["home"]) == Path(world.data["fake_home"])
    assert owner_touched == []
    # All file I/O stays in the caller area, Python installation, or source tree;
    # path resolution may inspect metadata of the caller area's exact ancestors.
    assert outside == [], outside[:10]
    assert report["seen"], "the probe must have recorded file activity"
    assert report["problems"] == [] and report["rows"] == 1
    assert _snap(world.owner_home) == before


def test_reverse_the_hook_catches_resolving_the_owner_paths(world):
    """The canary repeats an earlier mistake inside the probe; the hook must report it."""
    report, owner_touched, outside = _probe(world, canary=True)
    assert owner_touched, "the audit hook missed a resolution of the Owner's paths"
    assert any(p.startswith(os.path.normcase(str(world.owner_store))) for p in owner_touched)


def test_veto_wrapper_uses_pinned_limits_not_the_owner_shell(world):
    ids = [world.pr.admit(_card(m, "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"] for m in ("1", "2")]
    for item_id in ids:
        out = _launch(world, "veto", "retire", item_id, "--operator", "Owner", "--yes")
        assert out.returncode == 0, out.stderr[-2000:]
    archive = Path(world.data["root"]) / "knowledge" / "overflow_archive" / "lessons.jsonl"
    archived = [json.loads(l)["id"] for l in archive.read_text(encoding="utf-8").splitlines()] if archive.exists() else []
    assert archived == []  # the Owner shell's ENGRAM_RETIRED_MAX=1 never reached the child
    assert not (Path(world.data["root"]) / "approval_mode.json").exists()  # no strict latch from the shell
    assert all(r["result"] == "retired" for r in world.pr.receipts() if r["op"] == "veto_retire")


def test_reverse_with_the_owner_shell_limits_a_veto_retire_would_archive(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, limits={"r_max": 1})
    for m in ("1", "2"):
        item = w.pr.admit(_card(m, "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"]
        w.pr.owner_retire(item, "Owner")
    eng = w.pr._engram(read_only=True)
    assert [r for r in eng._read_overflow_archive("lesson") if not eng._is_snapshot_record(r)]


def test_launcher_veto_needs_yes(world):
    out = _launch(world, "veto", "retire", "x", "--operator", "Owner")
    assert out.returncode == 2


def test_a_write_that_keeps_failing_ends_in_a_receipt_not_a_crash(world, monkeypatch):
    from piia_engram.core import Engram

    monkeypatch.setattr(iso_mod, "IO_RETRY_SLEEP", 0)

    def _busy(self, *a, **k):
        raise PermissionError("file in use")

    monkeypatch.setattr(Engram, "add_lesson", _busy)
    receipt = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert receipt["result"] == "io_retry_exhausted"


def test_a_transient_write_error_is_retried(world, monkeypatch):
    from piia_engram.core import Engram

    monkeypatch.setattr(iso_mod, "IO_RETRY_SLEEP", 0)
    real = Engram.add_lesson
    calls = {"n": 0}

    def _flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("file in use")
        return real(self, *a, **k)

    monkeypatch.setattr(Engram, "add_lesson", _flaky)
    assert world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "admitted"
    assert calls["n"] == 2


def test_veto_receipts_name_the_operator(world):
    item = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"]
    receipt = world.pr.owner_retire(item, "Owner")
    assert receipt["op"] == "veto_retire" and receipt["operator"] == "Owner"


# ---------------------------------------------------------------------------
# 6. review R1 items (each with its reverse)
# ---------------------------------------------------------------------------


def test_reverse_without_the_wraps_the_canary_goes_unseen(world):
    """B1: the stat / lstat / realpath wraps are what catch a path resolution."""
    _report, owner_touched, _outside = _probe(world, canary=True, wraps=False)
    assert owner_touched == []  # the audit events alone never see it; the wrapped canary run does


def test_pin_deny_list_reads_no_environment(tmp_path, monkeypatch):
    """B1: only the paths passed in are resolved; the environment is never consulted."""
    elsewhere = tmp_path / "must_not_appear"
    for var in ("USERPROFILE", "HOME", "ENGRAM_DIR"):
        monkeypatch.setenv(var, str(elsewhere))
    out = tmp_path / "deny.json"
    pin_deny_list(out, owner_home=str(tmp_path / "given_home"), owner_engram_dir=str(tmp_path / "given_store"))
    text = out.read_text(encoding="utf-8")
    assert "must_not_appear" not in text and "given_home" in text and "given_store" in text
    with pytest.raises(ValueError):
        pin_deny_list(out, owner_home="", owner_engram_dir=str(tmp_path / "given_store"))
    cli = subprocess.run([sys.executable, "-m", "piia_engram.isolated_store_launch", "pin-deny-list", "--out", str(out)],
                         capture_output=True, text=True, env={**os.environ, "PYTHONPATH": SRC})
    assert cli.returncode == 2  # --owner-home and --owner-engram-dir are required


def _write_dp(world, name: str, mode: str, family: str, as_of: str) -> None:
    (world.dps / f"{name}.json").write_text(json.dumps({"kind": "D_VERDICT", "mode": mode, "family_code": family,
                                                        "as_of_utc": as_of}), encoding="utf-8")


def test_recall_never_goes_past_the_decision_points_own_time(world, monkeypatch):
    """B2: as_of_utc cuts evidence (every mode) and, for live, admission too."""
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    world.pr.admit(_card("2", "Q2", "2026-09-15T00:00:00Z"), "R4", ADMIT)  # evidence after the point
    _write_dp(world, "dp-live-past", "live", "Q3", HISTORICAL)
    _write_dp(world, "dp-replay-q3", "replay", "Q3", HISTORICAL)
    _write_dp(world, "dp-test-q3", "test", "Q3", HISTORICAL)
    # live at a past time: both cards were admitted after it, so nothing comes back
    assert _subjects(_recall(world.pr, "dp-live-past")) == []
    # replay and test: the caller passes a far-future evidence_before; the point's own time still cuts card 2
    assert _subjects(_recall(world.pr, "dp-replay-q3")) == ["1"]
    assert _subjects(_recall(world.pr, "dp-test-q3")) == ["1"]
    monkeypatch.setattr(IsolatedStore, "_effective_cuts",  # reverse: trust the caller's times
                        staticmethod(lambda mode, as_of, ev, adm: (ev, adm)))
    assert _subjects(_recall(world.pr, "dp-replay-q3")) == ["1", "2"]
    assert _subjects(_recall(world.pr, "dp-test-q3")) == ["1", "2"]
    assert _subjects(_recall(world.pr, "dp-live-past")) == ["1", "2"]


def test_the_owner_veto_reaches_a_card_in_the_overflow_archive(tmp_path, monkeypatch):
    """B3: reject works on an archived card, and the caller cannot restore it."""
    w = _make_world(tmp_path, monkeypatch, limits={"r_max": 1})
    ids = [w.pr.admit(_card(m, "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"] for m in ("1", "2")]
    time.sleep(0.01)
    before = utc_now_z()
    time.sleep(0.01)
    for item_id in ids:
        w.pr.retire(item_id, "R4", RETIRE)
    eng = w.pr._engram(read_only=True)
    archived = [r["id"] for r in eng._read_overflow_archive("lesson") if not eng._is_snapshot_record(r)]
    assert archived
    target = archived[0]
    with monkeypatch.context() as m:  # reverse: without the archive lookup the veto misses it
        m.setattr(IsolatedStore, "_archived_raw", lambda self, eng, item_id: None)
        assert w.pr.owner_reject(target, "Owner")["result"] == "not_found"
    assert w.pr.owner_reject(target, "Owner")["result"] == "tombstoned"
    assert w.pr.restore(target, "R4", RESTORE)["result"] in ("rejected_before", "owner_vetoed")
    assert target not in [i["id"] for i in _recall(w.pr, admitted_before=before)["items"]]
    assert w.pr.reconcile()["problems"] == []


def test_a_vetoed_card_cannot_be_restored_by_the_caller(world):
    item = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"]
    world.pr.owner_retire(item, "Owner")
    assert world.pr.restore(item, "R4", RESTORE)["result"] == "owner_vetoed"


def test_family_codes_are_validated_and_compared_case_insensitively(world, monkeypatch):
    """C1."""
    assert world.pr.admit(_card("1", "q1", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "admitted"
    assert world.pr.admit(_card("2", "Q 1", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "card_invalid"
    assert _subjects(_recall(world.pr, "dp-replay")) == []  # "q1" is family Q1
    monkeypatch.setattr(iso_mod, "_family_key", lambda value: str(value or ""))  # reverse: exact match
    assert _subjects(_recall(world.pr, "dp-replay")) == ["1"]


def test_a_torn_receipt_line_is_refused_not_a_crash(world):
    """C2."""
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    knowledge = Path(world.data["root"]) / "knowledge"
    before = _snap(knowledge)
    with world.pr.receipts_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "op": "adm')  # a torn last write
    assert world.pr.admit(_card("2", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] == "receipts_unreadable"
    assert world.pr.retire("x", "R4", RETIRE)["result"] == "receipts_unreadable"
    with pytest.raises(RecallRefused):
        _recall(world.pr)
    assert _snap(knowledge) == before
    assert world.pr.reconcile()["problems"][0].startswith("receipts_unreadable:")
    assert "receipts_unreadable" in (Path(world.data["receipts_dir"]) / "refusals.jsonl").read_text(encoding="utf-8")


def test_an_empty_query_returns_nothing(world):
    """C3: query None means no text filter; an empty query returns nothing (design s8)."""
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert _subjects(_recall(world.pr, query=None)) == ["1"]
    assert _subjects(_recall(world.pr, query="")) == []
    assert _subjects(_recall(world.pr, query="   ")) == []


def test_retire_and_restore_need_the_full_admission_record(world):
    """C4."""
    item = world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["item_id"]
    assert world.pr.retire(item, "R4", {"verdict": "retire"})["result"] == "admission_missing"
    assert world.pr.retire(item, "R4", RETIRE)["result"] == "retired"
    assert world.pr.restore(item, "R4", {"verdict": "restore"})["result"] == "admission_missing"
    assert world.pr.restore(item, "R4", RESTORE)["result"] == "restored"


def test_the_child_environment_is_minimal(world):
    """C5: other tools' paths, keys and the real home never reach the child."""
    parent = {"PATH": "p", "SYSTEMROOT": "s", "PYTHONPATH": SRC, "FASTEMBED_CACHE_PATH": "x", "CODEX_HOME": "x",
              "HF_HOME": "x", "OPENAI_API_KEY": "x", "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\owner",
              "XDG_CACHE_HOME": "x", "PIIA_OTHER": "x", "ENGRAM_DIR": str(world.owner_store)}
    env = build_child_env(parent, world.cfg)
    for gone in ("FASTEMBED_CACHE_PATH", "CODEX_HOME", "HF_HOME", "OPENAI_API_KEY", "PIIA_OTHER"):
        assert gone not in env
    assert env["PATH"] == "p" and env["SYSTEMROOT"] == "s"
    assert env["DO_NOT_TRACK"] == "1"
    fake = world.data["fake_home"]
    assert iso_mod._within(env["HOMEDRIVE"] + env["HOMEPATH"], fake)
    assert iso_mod._within(env["XDG_CACHE_HOME"], fake) and env["ENGRAM_DIR"] == world.data["root"]


@pytest.mark.parametrize("var,value", [("APPDATA", "OUTSIDE"), ("TEMP", "OUTSIDE"), ("FASTEMBED_CACHE_PATH", "x"),
                                       ("CODEX_HOME", "x"), ("HOME", "OUTSIDE"), ("USERPROFILE", "OUTSIDE")])
def test_the_guard_refuses_home_like_paths_outside_the_fake_home(world, monkeypatch, var, value):
    """C5: check_environment looks beyond Path.home()."""
    monkeypatch.setenv(var, str(world.tmp / value) if value == "OUTSIDE" else value)
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code in ("guard_home_not_redirected", "guard_env_foreign_path")


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows only")
def test_a_root_swapped_for_a_junction_after_open_is_refused(world, monkeypatch):
    """S4: the configured root is resolved again before every write."""
    root = Path(world.data["root"])
    moved = world.tmp / "elsewhere_store"
    root.rename(moved)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(moved)], check=True, capture_output=True)
    with pytest.raises(GuardRefused) as exc:
        world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert exc.value.code == "guard_root_changed"
    monkeypatch.setattr(iso_mod, "check_candidate", lambda path, deny, *, label: str(world.pr.root))  # reverse
    assert world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)["result"] != "guard_root_changed"


def _file_id(st) -> tuple[int, int]:
    return st.st_dev, st.st_ino


def test_receipts_are_fsynced(world, monkeypatch):
    """S3: an fsync on the receipts file itself, not just any fsync."""
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(_file_id(os.fstat(fd))), real(fd))[1])
    world.pr.admit(_card("1", "Q2", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    assert _file_id(os.stat(world.pr.receipts_path)) in synced


# ---------------------------------------------------------------------------
# 7. code review R2
# ---------------------------------------------------------------------------


def _tear_receipts(world) -> None:
    with world.pr.receipts_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "op": "adm')  # a torn last write


def test_clear_latch_refuses_a_torn_receipt_before_clearing(world, monkeypatch):
    """R2 change 2: no latch is cleared that no receipt records."""
    latch = Path(world.data["root"]) / "approval_mode.json"
    latch.write_text("{}", encoding="utf-8")
    _tear_receipts(world)
    store = IsolatedStore.open(world.cfg, allow_strict_latch=True)
    assert store.owner_clear_latch("Owner")["result"] == "receipts_unreadable"
    assert latch.exists()
    cli = _launch(world, "veto", "clear-latch", "--operator", "Owner", "--yes")
    assert cli.returncode == 4, cli.stdout + cli.stderr
    assert latch.exists()
    monkeypatch.setattr(IsolatedStore, "_receipts_problem", lambda self: "")  # reverse: act first, fail after
    with pytest.raises(ReceiptsUnreadable):
        store.owner_clear_latch("Owner")
    assert not latch.exists()


def test_rebind_refuses_a_torn_receipt_before_rebinding(world, monkeypatch):
    """R2 change 2: no marker is rebound that no receipt records."""
    marker_path = Path(world.data["root"]) / "isolated_store_root.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["file_id"] = marker["file_id"] + 1
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    moved = marker_path.read_bytes()
    _tear_receipts(world)
    store = IsolatedStore.open(world.cfg, allow_rebind=True)
    assert store.owner_rebind("Owner")["result"] == "receipts_unreadable"
    assert marker_path.read_bytes() == moved
    cli = _launch(world, "rebind", "--operator", "Owner", "--yes")
    assert cli.returncode == 4, cli.stdout + cli.stderr
    assert marker_path.read_bytes() == moved
    monkeypatch.setattr(IsolatedStore, "_receipts_problem", lambda self: "")  # reverse: act first, fail after
    with pytest.raises(ReceiptsUnreadable):
        store.owner_rebind("Owner")
    assert marker_path.read_bytes() != moved


def test_a_rebind_whose_replace_fails_leaves_no_temp_file(world, monkeypatch):
    from piia_engram import atomic_replace

    marker_path = Path(world.data["root"]) / "isolated_store_root.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["file_id"] = marker["file_id"] + 1
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    moved = marker_path.read_bytes()
    store = IsolatedStore.open(world.cfg, allow_rebind=True)
    real_replace = os.replace

    def _blocked(src, dst, *args, **kwargs):
        if Path(dst) == marker_path:
            exc = PermissionError(13, "Access is denied")
            exc.winerror = 32
            raise exc
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(atomic_replace, "_IS_WINDOWS", True)
    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.05)
    monkeypatch.setattr(os, "replace", _blocked)
    with pytest.raises(PermissionError):
        store.owner_rebind("Owner")

    assert marker_path.read_bytes() == moved
    assert not marker_path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("spelling", ["Q2-IRV", "q2_irv", "Q2.IRV"])
def test_family_spellings_fold_into_one_canonical_code(world, monkeypatch, spelling):
    """R2 change 3: upper case, "-" and "." as "_", before every family comparison."""
    assert iso_mod._family_key(spelling) == "Q2_IRV"
    world.pr.admit(_card("1", spelling, "2026-08-01T00:00:00Z"), "R4", ADMIT)
    world.pr.admit(_card("2", "Q3", "2026-08-01T00:00:00Z"), "R4", ADMIT)
    _write_dp(world, "dp-irv", "replay", "Q2_IRV", LATER)
    assert _subjects(_recall(world.pr, "dp-irv")) == ["2"]
    assert _subjects(_recall(world.pr, "dp-live", extra_exclude_families=["q2-irv"])) == ["2"]
    monkeypatch.setattr(iso_mod, "_family_key", lambda value: str(value or "").strip().casefold())  # reverse
    if spelling != "q2_irv":
        assert _subjects(_recall(world.pr, "dp-irv")) == ["1", "2"]


def test_the_guard_refuses_homedrive_homepath_outside_the_fake_home(world, monkeypatch):
    """R2 S2: the guard side of HOMEDRIVE + HOMEPATH."""
    fake = Path(world.data["fake_home"])
    monkeypatch.setenv("HOMEDRIVE", fake.drive)
    monkeypatch.setenv("HOMEPATH", str(fake)[len(fake.drive):])
    assert IsolatedStore.open(world.cfg)  # reverse: inside the fake home it opens
    monkeypatch.setenv("HOMEPATH", "\\Users\\owner")
    with pytest.raises(GuardRefused) as exc:
        IsolatedStore.open(world.cfg)
    assert exc.value.code == "guard_home_not_redirected"
