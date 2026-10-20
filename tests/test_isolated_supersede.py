"""Atomic version replacement, history and crash recovery using synthetic cards."""

import json
import os
import subprocess
import sys

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import GuardRefused, IsolatedStore, _sha256_json
from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import ADMIT, RESTORE, RETIRE, LATER, _card, _snap
from test_replay_experience import EARLY, CLOCK, _world

T1 = "2020-01-02T00:00:00Z"
T2 = "2020-01-02T12:00:00Z"
T3 = "2020-01-02T18:00:00Z"


def card(version):
    value = _card("entry", "Q2", EARLY, summary=f"Version {version} of the synthetic observation")
    value["detail"] = f"Synthetic revision {version}"
    return value


def admit(w, version, *, key=None, supersedes=None, at=T1):
    options = {"event_key": key}
    if supersedes is not None:
        options["supersedes"] = supersedes
    if w.pr.mode == "replay_experience":
        options.update(admitted_before=at, now=CLOCK)
    return w.pr.admit(card(version), "round", ADMIT, **options)


def recall(w, cut=LATER):
    options = {"now": CLOCK} if w.pr.mode == "replay_experience" else {}
    if w.pr.mode == "replay_experience" and cut == LATER:
        cut = "2020-01-03T00:00:00Z"
    return w.pr.recall("dp-test", "round", evidence_before="2020-01-03T00:00:00Z",
                       admitted_before=cut, **options)


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("key", [None, "sample:entry:v2"])
def test_one_completion_atomically_replaces_and_preserves_history(tmp_path, monkeypatch, mode, key):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    start = len(w.pr.receipts())
    new = admit(w, 2, key=key, supersedes=old["item_id"], at=T2)
    assert new["result"] == "admitted"
    assert new["supersedes"] == old["item_id"]
    assert new["superseded_by"] == new["item_id"]
    operations = w.pr.receipts()[start:]
    assert [r["op"] for r in operations] == ["admit_intent", "admit"]
    cut = T2 if mode == "replay_experience" else new["ts"]
    assert [r["id"] for r in recall(w, cut)["items"]] == [old["item_id"]]
    assert [r["id"] for r in recall(w)["items"]] == [new["item_id"]]
    assert old in w.pr.receipts()
    assert w.pr.reconcile()["problems"] == []
    rows = w.pr._rows(w.pr._engram(read_only=True))
    assert {r["id"] for r in rows if r.get("status") == "active"} == {new["item_id"]}
    assert admit(w, 2, key=key, supersedes=old["item_id"], at=T2)["result"] == "already_admitted"


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("reason,code", [("unknown", "supersedes_not_found"),
    ("retired", "supersedes_inactive"), ("vetoed", "supersedes_inactive"),
    ("superseded", "supersedes_inactive"), ("source_family", "supersedes_identity_mismatch"),
    ("subject_id", "supersedes_identity_mismatch"), ("source_decision_point", "supersedes_identity_mismatch"),
    ("invalid", "supersedes_invalid")])
def test_invalid_target_refuses_without_any_disk_write(tmp_path, monkeypatch, reason, code, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    target = old["item_id"]
    replacement = card(2)
    if reason == "unknown":
        target = "missing"
    elif reason == "invalid":
        target = "SYNTHETIC private free text"
    elif reason == "retired":
        w.pr.retire(target, "round", RETIRE)
    elif reason == "vetoed":
        w.pr.owner_retire(target, "Owner")
    elif reason == "superseded":
        admit(w, 3, supersedes=target)
    else:
        replacement[reason] = "Other.Entry" if reason == "source_decision_point" else "other"
    before = _snap(w.area)
    options = {"admitted_before": T2, "now": CLOCK} if mode == "replay_experience" else {}
    with pytest.raises(GuardRefused) as exc:
        w.pr.admit(replacement, "round", ADMIT, supersedes=target, **options)
    assert exc.value.code == code
    assert _snap(w.area) == before


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("key", [None, "sample:entry:v2"])
@pytest.mark.parametrize("crash", ["before_library", "after_library", "after_completion"])
def test_interrupted_supersede_is_all_or_nothing_and_retryable(tmp_path, monkeypatch, mode, key, crash):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    append = w.pr._append

    def stop(record):
        if record.get("op") == "admit" and record.get("result") == "admitted":
            if crash == "after_completion":
                append(record)
            raise SystemExit("synthetic crash")
        return append(record)

    with monkeypatch.context() as patch:
        if crash == "before_library":
            def before(*args, **kwargs):
                raise SystemExit("synthetic crash")
            patch.setattr(Engram, "add_lesson", before)
        else:
            patch.setattr(w.pr, "_append", stop)
        with pytest.raises(SystemExit):
            admit(w, 2, key=key, supersedes=old["item_id"], at=T2)
    w.pr = IsolatedStore.open(w.cfg)
    result = recall(w)
    assert len(result["items"]) == 1
    assert (result["items"][0]["id"] == old["item_id"]) == (crash != "after_completion")
    if crash != "after_completion":
        assert any(p.startswith("admission_") for p in w.pr.reconcile()["problems"])
    new = admit(w, 2, key=key, supersedes=old["item_id"], at=T2)
    assert new["result"] == ("already_admitted" if crash == "after_completion" else "admitted")
    assert [r["id"] for r in recall(w)["items"]] == [new["item_id"]]
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_restore_never_activates_two_versions_and_append_is_rollback(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    new = admit(w, 2, supersedes=old["item_id"], at=T2)
    before = _snap(w.area)
    with pytest.raises(GuardRefused) as exc:
        w.pr.restore(old["item_id"], "round", RESTORE)
    assert exc.value.code == "superseded_active"
    assert _snap(w.area) == before
    rollback = admit(w, 1, supersedes=new["item_id"], at=T3)
    assert rollback["result"] == "admitted" and rollback["item_id"] != old["item_id"]
    with pytest.raises(GuardRefused, match="superseded_active"):
        w.pr.restore(old["item_id"], "round", RESTORE)
    w.pr.retire(rollback["item_id"], "round", RETIRE)
    assert w.pr.restore(old["item_id"], "round", RESTORE)["result"] == "restored"
    with pytest.raises(GuardRefused, match="superseded_active"):
        w.pr.restore(new["item_id"], "round", RESTORE)


def test_logical_lifecycle_cannot_overlap_supersede_history(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    old = admit(w, 1)
    new = admit(w, 2, supersedes=old["item_id"], at=T2)
    w.pr.retire(new["item_id"], "round", RETIRE, effective_at=T3, now=CLOCK)
    with pytest.raises(GuardRefused, match="superseded_active"):
        w.pr.restore(old["item_id"], "round", RESTORE, effective_at="2020-01-02T15:00:00Z", now=CLOCK)
    assert w.pr.restore(old["item_id"], "round", RESTORE, effective_at=T3, now=CLOCK)["result"] == "restored"
    assert [r["id"] for r in recall(w, T2)["items"]] == [old["item_id"]]
    assert [r["id"] for r in recall(w, T3)["items"]] == [new["item_id"]]
    assert [r["id"] for r in recall(w)["items"]] == [old["item_id"]]
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_supersede_retry_in_fresh_process(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    new = admit(w, 2, key="sample:v2", supersedes=old["item_id"], at=T2)
    options = {"supersedes": old["item_id"], "event_key": "sample:v2"}
    if mode == "replay_experience":
        options.update(admitted_before=T2, now=CLOCK)
    program = "\n".join([
        "import json,sys,os", "os.environ.pop('CODEX_HOME',None)",
        "from piia_engram.isolated_store import Config,IsolatedStore",
        "s=IsolatedStore.open(Config.load(sys.argv[1]))",
        "r=s.admit(json.loads(sys.argv[2]),'round',json.loads(sys.argv[3]),**json.loads(sys.argv[4]))",
        "print(json.dumps({k:r[k] for k in ('result','item_id')}))",
    ])
    env = build_child_env(dict(os.environ), w.cfg)
    env["CODEX_HOME"] = str(w.cfg.fake_home / ".codex")
    result = subprocess.run([sys.executable, "-c", program, str(w.cfg_path), json.dumps(card(2)),
                             json.dumps(ADMIT), json.dumps(options)], env=env,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"result": "already_admitted", "item_id": new["item_id"]}


@pytest.mark.parametrize("key", [None, "sample:v2"])
def test_replacement_at_capacity_archival_and_repeated_restore(tmp_path, monkeypatch, key):
    w = _world(tmp_path, monkeypatch, mode="production", limits={"soft_cap": 1, "hard_cap": 1, "r_max": 1})
    old = admit(w, 1)
    second = admit(w, 2, supersedes=old["item_id"], key=key)
    third = admit(w, 3, supersedes=second["item_id"])
    assert second["result"] == third["result"] == "admitted"
    assert [r["id"] for r in recall(w)["items"]] == [third["item_id"]]
    assert [r["id"] for r in recall(w, second["ts"])["items"]] == [old["item_id"]]
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("field,value", [("source_decision_point", "DP-SRC-ENTRY"),
                                         ("source_decision_point", " dp-src-entry "),
                                         ("source_decision_point", "dp-src-entry.2")])
def test_entry_identity_is_opaque_case_sensitive_and_not_normalized(tmp_path, monkeypatch, field, value):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    replacement = card(2)
    replacement[field] = value
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersedes_identity_mismatch"):
        w.pr.admit(replacement, "round", ADMIT, supersedes=old["item_id"])
    assert _snap(w.area) == before


@pytest.mark.parametrize("change", ["target", "content", "clock", "admission"])
def test_pending_retry_drift_is_no_write_and_does_not_cancel_intent(tmp_path, monkeypatch, change):
    w = _world(tmp_path, monkeypatch)
    old = admit(w, 1)
    append = w.pr._append
    def stop(record):
        if record.get("op") == "admit" and record.get("result") == "admitted":
            raise SystemExit("synthetic crash")
        return append(record)
    with monkeypatch.context() as patch:
        patch.setattr(w.pr, "_append", stop)
        with pytest.raises(SystemExit):
            admit(w, 2, supersedes=old["item_id"], key="sample:v2", at=T2)
    args = dict(supersedes=old["item_id"], event_key="sample:v2", admitted_before=T2, now=CLOCK)
    replacement, admission = card(2), dict(ADMIT)
    if change == "target":
        args["supersedes"] = "other"
    elif change == "content":
        replacement["detail"] += " changed"
    elif change == "clock":
        args["admitted_before"] = T3
    else:
        admission["decision_record_id"] = "other"
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="event_key_conflict"):
        w.pr.admit(replacement, "round", admission, **args)
    assert _snap(w.area) == before
    assert admit(w, 2, supersedes=old["item_id"], key="sample:v2", at=T2)["result"] == "admitted"


def test_pending_supersede_locks_version_lifecycle_and_can_abandon_before_write(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    with monkeypatch.context() as patch:
        def stop(*args, **kwargs):
            raise SystemExit("synthetic crash")
        patch.setattr(Engram, "add_lesson", stop)
        with pytest.raises(SystemExit):
            admit(w, 2, supersedes=old["item_id"], key="sample:v2")
    intent = w.pr.receipts()[-1]
    before = _snap(w.area)
    for method, verdict in (("retire", RETIRE), ("restore", RESTORE)):
        with pytest.raises(GuardRefused, match="supersede_pending"):
            getattr(w.pr, method)(old["item_id"], "round", verdict)
    with pytest.raises(GuardRefused, match="supersede_pending"):
        admit(w, 3, supersedes=old["item_id"])
    assert _snap(w.area) == before
    result = w.pr.abandon_intent("sample:v2", intent_receipt_seq=intent["seq"],
                               intent_receipt_sha256=_sha256_json(intent))
    assert result["result"] == "abandoned"
    assert w.pr.reconcile()["problems"] == []


def test_backdated_supersede_cannot_precede_original_admission(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    old = admit(w, 1, at=T2)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersedes_before_admission"):
        admit(w, 2, supersedes=old["item_id"], at=T1)
    assert _snap(w.area) == before


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_crash_at_atomic_library_replace_in_subprocess(tmp_path, monkeypatch, phase):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    program = "\n".join([
        "import json,sys,os,pathlib", "os.environ.pop('CODEX_HOME',None)",
        "from piia_engram.isolated_store import Config,IsolatedStore",
        "from piia_engram import storage",
        "s=IsolatedStore.open(Config.load(sys.argv[1]))",
        "original=storage.replace_with_retry",
        "def replace(path,target):",
        "    if pathlib.Path(target)==s.root/'knowledge'/'lessons.json':",
        "        if sys.argv[3]=='after_replace': original(path,target)",
        "        os._exit(73)",
        "    return original(path,target)",
        "storage.replace_with_retry=replace",
        "s.admit(json.loads(sys.argv[2]),'round',json.loads(sys.argv[5]),supersedes=sys.argv[4],event_key='sample:v2')",
    ])
    env = build_child_env(dict(os.environ), w.cfg)
    env["CODEX_HOME"] = str(w.cfg.fake_home / ".codex")
    result = subprocess.run([sys.executable, "-c", program, str(w.cfg_path), json.dumps(card(2)),
                             phase, old["item_id"], json.dumps(ADMIT)], env=env,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 73, result.stderr
    assert [r["id"] for r in recall(w)["items"]] == [old["item_id"]]
    assert admit(w, 2, supersedes=old["item_id"], key="sample:v2")["result"] == "admitted"
    assert w.pr.reconcile()["problems"] == []


@pytest.mark.parametrize("change", ["link", "receipt", "identity", "active"])
def test_reconcile_reports_supersede_drift(tmp_path, monkeypatch, change):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    new = admit(w, 2, supersedes=old["item_id"])
    path = w.pr.root / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if change == "receipt":
        lines = w.pr.receipts_path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record["superseded_by"] = "wrong"
        lines[-1] = json.dumps(record)
        w.pr.receipts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        row = next(r for r in rows if r["id"] == old["item_id"])
        row[{"link": "superseded_by", "identity": "source_decision_point", "active": "status"}[change]] = (
            "active" if change == "active" else "wrong")
        path.write_text(json.dumps(rows), encoding="utf-8")
    problems = w.pr.reconcile()["problems"]
    assert any(p.startswith("supersede_") for p in problems), problems


def test_os_error_after_atomic_write_finishes_supersede_instead_of_closing_intent(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    add = Engram.add_lesson
    def interrupted(eng, *args, **kwargs):
        result = add(eng, *args, **kwargs)
        raise OSError("synthetic failure after commit")
    with monkeypatch.context() as patch:
        patch.setattr(Engram, "add_lesson", interrupted)
        patch.setattr("piia_engram.isolated_store.IO_RETRY_SLEEP", 0)
        new = admit(w, 2, supersedes=old["item_id"])
    assert new["result"] == "admitted"
    assert [r["id"] for r in recall(w)["items"]] == [new["item_id"]]
    assert w.pr.reconcile()["problems"] == []
