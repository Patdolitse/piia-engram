"""Independent replay provenance and audited legacy rename recovery."""

from __future__ import annotations

import hashlib
import json

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    CONFIG_ENV, Config, GuardRefused, IsolatedStore, LIMITS_FILE, MARKER, RECEIPTS_FILE, root_mode,
)
from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import _snap
from test_replay_attachment_exports import legacy_production_root
from test_replay_experience import MODE, _admit, _world
from test_replay_legacy_attachment import _assert_refusal


REPLAY_SIGNAL = "replay_experience.marker"


def _strip_mode_metadata(root):
    path = root / MARKER
    marker = json.loads(path.read_text(encoding="utf-8"))
    for field in ("mode", "receipts_dir", "root_id"):
        marker.pop(field)
    path.write_text(json.dumps(marker), encoding="utf-8")


@pytest.mark.parametrize("mode", [MODE, "production"])
def test_init_records_independent_replay_marker(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    signal = w.pr.root / REPLAY_SIGNAL
    initial = w.pr.receipts()[0]
    if mode == MODE:
        assert signal.read_text(encoding="utf-8") == "store_mode: replay_experience\n"
        assert initial["replay_marker"] == REPLAY_SIGNAL
        assert initial["replay_marker_sha256"] == hashlib.sha256(signal.read_bytes()).hexdigest()
    else:
        assert not signal.exists()
        assert "replay_marker" not in initial
        assert "replay_marker_sha256" not in initial


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("export", ["context", "native"])
def test_no_locator_three_field_loss_refuses_before_export(tmp_path, monkeypatch, read_only, export):
    w = _world(tmp_path, monkeypatch)
    assert _admit(w)["result"] == "admitted"
    _strip_mode_metadata(w.pr.root)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    before, ledger = _snap(w.pr.root), w.pr.receipts_path.read_bytes()
    output = tmp_path / "export.json"
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        eng = Engram(root=w.pr.root, read_only=read_only)
        if export == "context":
            eng.generate_context_report(level="quick")
        else:
            eng.export_all(str(output))
    assert _snap(w.pr.root) == before
    assert w.pr.receipts_path.read_bytes() == ledger
    assert not output.exists()
    _assert_refusal(w.pr.root.with_name(w.pr.root.name + "_guard"), "guard_mode_immutable")


@pytest.mark.parametrize("signal", ["file", "lesson", "decision", "lesson_archive", "decision_archive"])
@pytest.mark.parametrize("metadata", ["legacy", "absent"])
def test_each_replay_signal_independently_blocks_legacy_fallback(tmp_path, monkeypatch, signal, metadata):
    w = _world(tmp_path, monkeypatch)
    # Isolate each provenance source; no launcher or external ledger locator.
    dedicated = w.pr.root / REPLAY_SIGNAL
    dedicated.unlink(missing_ok=True)
    if signal == "file":
        dedicated.write_text("", encoding="utf-8")  # overwritten content remains a signal
    else:
        knowledge = w.pr.root / "knowledge"
        archived = signal.endswith("_archive")
        kind = signal.split("_")[0]
        directory = knowledge / "overflow_archive" if archived else knowledge
        directory.mkdir(parents=True, exist_ok=True)
        row = {"id": "sample", "summary": "Replay cache observation", "store_mode": MODE}
        path = directory / (f"{kind}s.jsonl" if archived else f"{kind}s.json")
        path.write_text(json.dumps(row) + "\n" if archived else json.dumps([row]), encoding="utf-8")
    _strip_mode_metadata(w.pr.root)
    if metadata == "absent":
        (w.pr.root / MARKER).unlink()
        (w.pr.root / LIMITS_FILE).unlink()
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    before, ledger = _snap(w.pr.root), w.pr.receipts_path.read_bytes()
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.pr.root, read_only=True)
    assert _snap(w.pr.root) == before
    assert w.pr.receipts_path.read_bytes() == ledger
    _assert_refusal(w.pr.root.with_name(w.pr.root.name + "_guard"), "guard_mode_immutable")


def _rename(w, monkeypatch):
    destination = w.cfg.root.with_name("renamed_root")
    w.cfg.root.rename(destination)
    w.data["root"] = str(destination)
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    w.cfg = Config.load(w.cfg_path)
    for key, value in build_child_env({}, w.cfg).items():
        monkeypatch.setenv(key, value)
    return destination


def test_genuine_legacy_rename_requires_and_accepts_owner_rebind(legacy_production_root, monkeypatch):
    w = legacy_production_root
    ledger_before = (w.cfg.receipts_dir / RECEIPTS_FILE).read_bytes()
    destination = _rename(w, monkeypatch)
    marker_before = (destination / MARKER).read_bytes()
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        IsolatedStore.open(w.cfg)
    assert (destination / MARKER).read_bytes() == marker_before
    assert (w.cfg.receipts_dir / RECEIPTS_FILE).read_bytes() == ledger_before
    store = IsolatedStore.open(w.cfg, allow_rebind=True)
    # Recovery authorization must not enable ordinary reads before the rebind.
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        store._engram(read_only=True)
    assert store.owner_rebind("Owner")["result"] == "rebound"
    marker = json.loads((destination / MARKER).read_text(encoding="utf-8"))
    assert marker["realpath"] == str(destination.resolve())
    assert not any(field in marker for field in ("mode", "receipts_dir", "root_id"))
    assert store.receipts_path.read_bytes().startswith(ledger_before)
    assert store.receipts()[-1]["op"] == "rebind"
    assert store.receipts()[-1]["operator"] == "Owner"
    assert IsolatedStore.open(w.cfg).mode == "production"
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    eng = Engram(root=destination, read_only=True)
    assert eng.get_lessons(_update_access=False) == w.expected_lessons
    assert eng.get_profile() == w.expected_profile


@pytest.mark.parametrize("signal", ["file", "lesson", "lesson_archive"])
def test_owner_rebind_cannot_convert_disguised_replay_to_production(tmp_path, monkeypatch, signal):
    w = _world(tmp_path, monkeypatch)
    assert _admit(w)["result"] == "admitted"
    dedicated = w.pr.root / REPLAY_SIGNAL
    if signal == "file":
        dedicated.write_text("store_mode: replay_experience\n", encoding="utf-8")
        (w.pr.root / "knowledge" / "lessons.json").write_text("[]", encoding="utf-8")
    else:
        dedicated.unlink(missing_ok=True)
        if signal == "lesson_archive":
            path = w.pr.root / "knowledge" / "lessons.json"
            rows = json.loads(path.read_text(encoding="utf-8"))
            archive = path.parent / "overflow_archive" / "lessons.jsonl"
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            path.write_text("[]", encoding="utf-8")
    _strip_mode_metadata(w.pr.root)
    # Remove external contradictions to exercise in-root provenance on recovery.
    initial = w.pr.receipts()[0]
    for field in ("store_mode", "root_id", "replay_marker", "replay_marker_sha256"):
        initial.pop(field, None)
    w.pr.receipts_path.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    w.data["mode"] = "production"
    destination = _rename(w, monkeypatch)
    before, ledger = _snap(destination), w.pr.receipts_path.read_bytes()
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        IsolatedStore.open(w.cfg, allow_rebind=True).owner_rebind("Owner")
    assert _snap(destination) == before
    assert w.pr.receipts_path.read_bytes() == ledger
    _assert_refusal(w.cfg.receipts_dir, "guard_mode_immutable")


def test_rebind_still_rejects_a_different_legacy_directory(legacy_production_root, monkeypatch):
    w = legacy_production_root
    destination = _rename(w, monkeypatch)
    path = destination / MARKER
    marker = json.loads(path.read_text(encoding="utf-8"))
    marker["file_id"] += 1
    path.write_text(json.dumps(marker), encoding="utf-8")
    before = _snap(destination)
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        IsolatedStore.open(w.cfg, allow_rebind=True).owner_rebind("Owner")
    assert _snap(destination) == before


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("archive", [False, True])
def test_unmarked_corruption_keeps_existing_production_attachment(legacy_production_root, isolated, archive):
    w = legacy_production_root
    if not isolated:
        (w.cfg.root / MARKER).unlink()
        (w.cfg.root / LIMITS_FILE).unlink()
    path = w.cfg.root / "knowledge" / "lessons.json"
    if archive:
        path = path.parent / "overflow_archive" / "lessons.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn unmarked data", encoding="utf-8")
    before = _snap(w.cfg.root)
    assert root_mode(w.cfg.root, "production") == "production"
    assert _snap(w.cfg.root) == before


@pytest.mark.parametrize("archive", [False, True])
def test_stored_signal_survives_unrelated_torn_data(legacy_production_root, archive):
    w = legacy_production_root
    path = w.cfg.root / "knowledge" / "lessons.json"
    if archive:
        path = path.parent / "overflow_archive" / "lessons.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = '{torn unmarked data\n{"store_mode": "replay_experience"}\n'
    else:
        text = '[{"store_mode": "replay_experience"}, {torn data'
    path.write_text(text, encoding="utf-8")
    before = _snap(w.cfg.root)
    with pytest.raises(GuardRefused, match="replay root signal"):
        root_mode(w.cfg.root, "production")
    assert _snap(w.cfg.root) == before


def test_legacy_rename_rebind_precedes_version_upgrade(legacy_production_root, monkeypatch):
    w = legacy_production_root
    ledger = w.cfg.receipts_dir / RECEIPTS_FILE
    initial = json.loads(ledger.read_text(encoding="utf-8"))
    initial["lib_version"] = "4.22.0"
    ledger.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    destination = _rename(w, monkeypatch)
    before, ledger_before = _snap(destination), ledger.read_bytes()
    store = IsolatedStore.open(w.cfg, allow_rebind=True)
    assert _snap(destination) == before
    assert ledger.read_bytes() == ledger_before
    assert store.owner_rebind("Owner")["result"] == "rebound"
    reopened = IsolatedStore.open(w.cfg)
    assert reopened.receipts()[-1]["op"] == "version"
    assert reopened.receipts()[-1]["result"] == "upgraded"
