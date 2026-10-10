"""Legacy attachments require positive identity and no contradictory ledger."""

from __future__ import annotations

import json

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    CONFIG_ENV, GuardRefused, MARKER, RECEIPTS_FILE, root_mode,
)
from test_isolated_store import _snap
from test_replay_attachment_exports import legacy_production_root
from test_replay_experience import _world


def _assert_refusal(audit_dir, code):
    rows = [json.loads(line) for line in
            (audit_dir / "refusals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["code"] == code
    assert set(rows[0]) == {"op", "result", "code", "ts", "pid"}


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("export", ["context", "native"])
def test_copied_legacy_marker_cannot_export_replay_content(
        legacy_production_root, tmp_path, monkeypatch, configured, read_only, export):
    legacy = legacy_production_root
    w = _world(tmp_path / "replay", monkeypatch)
    w.pr._engram(read_only=False)
    (w.pr.root / "identity" / "profile.json").write_text(
        json.dumps({"name": "Replay sample", "role": "Generic cache maintainer"}),
        encoding="utf-8",
    )
    (w.pr.root / MARKER).write_bytes((legacy.cfg.root / MARKER).read_bytes())
    if not configured:
        monkeypatch.delenv(CONFIG_ENV, raising=False)
    before = _snap(w.pr.root)
    ledger_before = w.pr.receipts_path.read_bytes()
    output = tmp_path / "export.json"
    code = "guard_mode_immutable"
    with pytest.raises(GuardRefused, match=code):
        eng = Engram(root=w.pr.root, read_only=read_only)
        if export == "context":
            eng.generate_context_report(level="quick")
        else:
            eng.export_all(str(output))
    assert _snap(w.pr.root) == before
    assert w.pr.receipts_path.read_bytes() == ledger_before
    assert not output.exists()
    audit_dir = w.pr.receipts_dir if configured else w.pr.root.with_name(w.pr.root.name + "_guard")
    _assert_refusal(audit_dir, code)


@pytest.mark.parametrize("entrypoint", ["engram", "explicit_ledger", "both_ledgers"])
def test_simultaneous_loss_of_replay_binding_fields_is_refused(tmp_path, monkeypatch, entrypoint):
    w = _world(tmp_path, monkeypatch)
    marker_path = w.pr.root / MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    for field in ("mode", "receipts_dir", "root_id"):
        marker.pop(field)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    # Even a production launcher cannot override the intact replay receipt.
    w.data["mode"] = "production"
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    audit_dir = w.pr.receipts_dir
    before = _snap(w.pr.root)
    ledger_before = w.pr.receipts_path.read_bytes()
    if entrypoint == "explicit_ledger":
        monkeypatch.delenv(CONFIG_ENV, raising=False)
    elif entrypoint == "both_ledgers":
        audit_dir = tmp_path / "alternate_receipts"
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        if entrypoint == "engram":
            Engram(root=w.pr.root, read_only=True)
        else:
            root_mode(w.pr.root, "production", receipts_dir=audit_dir)
    assert _snap(w.pr.root) == before
    assert w.pr.receipts_path.read_bytes() == ledger_before
    _assert_refusal(audit_dir, "guard_mode_immutable")


@pytest.mark.parametrize("field", ["realpath", "volume", "file_id"])
@pytest.mark.parametrize("change", ["missing", "mismatch"])
def test_legacy_identity_must_be_positively_confirmed(legacy_production_root, field, change):
    w = legacy_production_root
    marker_path = w.cfg.root / MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if change == "missing":
        marker.pop(field)
    else:
        marker[field] = str(w.tmp / "another_root") if field == "realpath" else marker[field] + 1
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = _snap(w.cfg.root)
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        Engram(root=w.cfg.root, read_only=True)
    assert _snap(w.cfg.root) == before
    _assert_refusal(w.cfg.root.with_name(w.cfg.root.name + "_guard"), "guard_root_binding")


@pytest.mark.parametrize("ledger_state", ["legacy", "missing"])
@pytest.mark.parametrize("read_only", [False, True])
def test_genuine_legacy_root_with_config_keeps_reads_and_metadata(
        legacy_production_root, monkeypatch, read_only, ledger_state):
    w = legacy_production_root
    monkeypatch.setenv(CONFIG_ENV, str(w.cfg_path))
    marker_before = (w.cfg.root / MARKER).read_bytes()
    ledger_path = w.cfg.receipts_dir / RECEIPTS_FILE
    ledger_before = ledger_path.read_bytes()
    if ledger_state == "missing":
        ledger_path.unlink()
    eng = Engram(root=w.cfg.root, read_only=read_only)
    assert eng._store_mode == "production"
    assert eng.get_lessons(_update_access=False) == w.expected_lessons
    assert eng.get_profile() == w.expected_profile
    assert "store_mode: replay_experience" not in eng.generate_context(level="quick")
    assert (w.cfg.root / MARKER).read_bytes() == marker_before
    if ledger_state == "legacy":
        assert ledger_path.read_bytes() == ledger_before
    else:
        assert not ledger_path.exists()
    assert not (w.cfg.receipts_dir / "refusals.jsonl").exists()


@pytest.mark.parametrize("ledger_state", ["empty", "invalid_json", "wrong_header", "modern_production"])
def test_present_ledger_cannot_be_silently_ignored(legacy_production_root, monkeypatch, ledger_state):
    w = legacy_production_root
    monkeypatch.setenv(CONFIG_ENV, str(w.cfg_path))
    ledger_path = w.cfg.receipts_dir / RECEIPTS_FILE
    initial = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger_state == "empty":
        text = ""
    elif ledger_state == "invalid_json":
        text = "{"
    else:
        if ledger_state == "wrong_header":
            initial["op"] = "recall"
        else:
            initial["store_mode"] = "production"
            initial["root_id"] = "modern-root"
        text = json.dumps(initial) + "\n"
    ledger_path.write_text(text, encoding="utf-8")
    before = _snap(w.cfg.root)
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.cfg.root, read_only=True)
    assert _snap(w.cfg.root) == before
    _assert_refusal(w.cfg.receipts_dir, "guard_mode_immutable")
