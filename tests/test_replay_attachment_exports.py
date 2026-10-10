"""Root binding, typed exports, and pre-replay production layout compatibility."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    GuardRefused, IsolatedStore, MARKER, RECEIPTS_FILE, REPLAY_EXPORT_MARKER,
    carries_replay_marker,
)
from test_isolated_store import _make_world, _snap
from test_replay_experience import MODE, _world


@pytest.mark.parametrize("source_mode", ["production", MODE])
@pytest.mark.parametrize("read_only", [False, True])
def test_another_roots_initialization_ledger_is_refused(tmp_path, monkeypatch, source_mode, read_only):
    source = _world(tmp_path / "source", monkeypatch, mode=source_mode)
    other = _world(tmp_path / "other", monkeypatch, mode="production")
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    marker_path = source.pr.root / MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker.update(mode="production", receipts_dir=str(other.pr.receipts_dir))
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = _snap(source.pr.root)
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        Engram(root=source.pr.root, read_only=read_only)
    assert _snap(source.pr.root) == before
    refusals = [json.loads(line) for line in (other.pr.receipts_dir / "refusals.jsonl").read_text().splitlines()]
    assert refusals[-1]["code"] == "guard_root_binding"
    assert len(refusals) == 1


@pytest.mark.parametrize("mode", ["production", MODE])
def test_initialization_receipt_binds_the_actual_root(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    marker = json.loads((w.pr.root / MARKER).read_text())
    initial = w.pr.receipts()[0]
    assert marker["root_id"] == initial["root_id"]
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    assert Engram(root=w.pr.root, read_only=True, store_mode=mode)._store_mode == mode
    marker["root_id"] = "unrelated-root"
    (w.pr.root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        Engram(root=w.pr.root, read_only=True, store_mode=mode)


def test_copying_an_entire_ledger_and_marker_does_not_bind_another_root(tmp_path, monkeypatch):
    source = _world(tmp_path / "source", monkeypatch)
    other = _world(tmp_path / "other", monkeypatch)
    marker = json.loads((source.pr.root / MARKER).read_text())
    marker["receipts_dir"] = str(other.pr.receipts_dir)
    (other.pr.root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    other.pr.receipts_path.write_bytes(source.pr.receipts_path.read_bytes())
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    with pytest.raises(GuardRefused, match="guard_root_binding"):
        Engram(root=other.pr.root, read_only=True, store_mode=MODE)


@pytest.mark.parametrize("field", ["mode", "receipts_dir", "root_id"])
def test_replay_metadata_requires_all_new_binding_fields(tmp_path, monkeypatch, field):
    w = _world(tmp_path, monkeypatch)
    marker = json.loads((w.pr.root / MARKER).read_text())
    marker.pop(field, None)
    (w.pr.root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(GuardRefused):
        Engram(root=w.pr.root, read_only=True, store_mode=MODE)


@pytest.mark.parametrize("mode", ["production", MODE])
@pytest.mark.parametrize("max_tokens", [None, 40])
def test_context_tuple_preserves_text_provenance_and_omission_contract(tmp_path, monkeypatch, mode, max_tokens):
    w = _world(tmp_path / "source", monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    (eng.root / "identity" / "profile.json").write_text(
        json.dumps({"name": "Sample", "role": "Generic cache maintainer", "expertise": ["bounded caches"]}),
        encoding="utf-8",
    )
    eng.add_lesson({"summary": "generic cache observation " * 20, "tier": "verified"})
    options = dict(level="standard", max_tokens=max_tokens)
    raw_text, raw_omitted = Engram.generate_context_report.__wrapped__(eng, **options)
    result = eng.generate_context_report(**options)
    assert isinstance(result, tuple) and len(result) == 2
    text, omitted = result
    assert omitted == raw_omitted
    assert "store_mode" not in (omitted or {})
    if max_tokens is not None:
        assert omitted is not None
    assert text == (REPLAY_EXPORT_MARKER + "\n" if mode == MODE else "") + raw_text
    assert carries_replay_marker(result) == (mode == MODE)
    assert eng.generate_context(**options) == text
    assert eng.last_context_omitted == omitted
    normal = Engram(root=tmp_path / "normal")
    before = _snap(normal.root)
    imported = normal.add_lesson({"summary": "copied context", "detail": text})
    if mode == MODE:
        assert imported["error"] == "replay_experience_import_refused"
        assert _snap(normal.root) == before
    else:
        assert not imported.get("error")


@pytest.mark.parametrize("surface", ["quick_snapshot", "native_path", "review_path"])
@pytest.mark.parametrize("mode", ["production", MODE])
def test_path_returning_exports_mark_file_content_and_downstream_refuses(tmp_path, monkeypatch, surface, mode):
    w = _world(tmp_path / "source", monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    if surface == "quick_snapshot":
        result = eng.refresh_quick_context(target=str(tmp_path / "quick.md"), level="quick")
    elif surface == "native_path":
        result = eng.export_all(str(tmp_path / "native.json"))
    else:
        result = eng.export_review_page()
    assert isinstance(result, (Path, str))
    text = Path(result).read_text(encoding="utf-8")
    payload = json.loads(text) if surface == "native_path" else text
    assert carries_replay_marker(payload) == (mode == MODE)
    normal = Engram(root=tmp_path / "normal")
    imported = normal.add_lesson({"summary": "copied export", "export": payload})
    assert (imported.get("error") == "replay_experience_import_refused") == (mode == MODE)


@pytest.mark.parametrize("mode", ["production", MODE])
@pytest.mark.parametrize("surface", ["preview_text", "preview_html", "preview_path", "weekly_text"])
def test_public_report_renderers_keep_root_provenance(tmp_path, monkeypatch, mode, surface):
    from piia_engram.context_preview import (
        build_context_preview, render_context_preview_text, render_context_preview_html,
        write_context_preview_html,
    )
    from piia_engram.reports_weekly import build_weekly_recap, render_weekly_text

    w = _world(tmp_path / "source", monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    eng.add_lesson({"summary": "generic cache observation", "tier": "verified"})
    if surface == "weekly_text":
        result = render_weekly_text(build_weekly_recap(eng))
    else:
        preview = build_context_preview(eng, query="cache")
        if surface == "preview_text":
            result = render_context_preview_text(preview)
        elif surface == "preview_html":
            result = render_context_preview_html(preview)
        else:
            path = write_context_preview_html(preview, eng.root, tmp_path / "preview.html")
            assert isinstance(path, Path)
            result = path.read_text(encoding="utf-8")
    assert result.startswith(REPLAY_EXPORT_MARKER) == (mode == MODE)
    normal = Engram(root=tmp_path / "normal")
    imported = normal.add_lesson({"summary": "copied report", "detail": result})
    assert (imported.get("error") == "replay_experience_import_refused") == (mode == MODE)


@pytest.fixture
def legacy_production_root(tmp_path, monkeypatch):
    """The metadata/ledger shapes emitted by 86547e88b, without replay init."""
    w = _make_world(tmp_path / "legacy", monkeypatch, init=False)
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    eng = Engram(root=w.cfg.root)
    eng.add_lesson({"summary": "legacy cache observation", "tier": "verified"})
    (eng.root / "identity" / "profile.json").write_text(
        json.dumps({"name": "Legacy sample", "role": "Generic maintainer"}), encoding="utf-8",
    )
    w.expected_lessons = eng.get_lessons(_update_access=False)
    w.expected_profile = eng.get_profile()
    stat = eng.root.stat()
    marker = {"purpose": "isolated-store", "created_at": "2020-01-01T00:00:00Z",
              "realpath": str(eng.root.resolve()), "volume": stat.st_dev, "file_id": stat.st_ino}
    (eng.root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    (eng.root / "isolated_store_limits.json").write_text(json.dumps(w.cfg.limits), encoding="utf-8")
    (eng.root / "telemetry_config.json").write_text(json.dumps({"reconcile_authorized": False}), encoding="utf-8")
    w.cfg.receipts_dir.mkdir(parents=True)
    initial = {"seq": 1, "prev_sha256": "", "ts": "2020-01-01T00:00:00Z", "pid": 1,
               "proc_started_utc": "2020-01-01T00:00:00Z", "lib_version": "4.23.0",
               "limits": w.cfg.limits, "root_state_sha256": "", "op": "init", "result": "initialised"}
    parts = []
    for relative in ("lessons.json", "decisions.json", "tombstones.jsonl"):
        path = eng.root / "knowledge" / relative
        parts.append((relative, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""))
    initial["root_state_sha256"] = hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    ).hexdigest()
    (w.cfg.receipts_dir / RECEIPTS_FILE).write_text(json.dumps(initial) + "\n", encoding="utf-8")
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    return w


@pytest.mark.parametrize("read_only", [False, True])
def test_pre_replay_layout_attaches_and_reads_without_launcher_config(legacy_production_root, read_only):
    w = legacy_production_root
    marker_before = (w.cfg.root / MARKER).read_bytes()
    ledger_before = (w.cfg.receipts_dir / RECEIPTS_FILE).read_bytes()
    before = _snap(w.cfg.root)
    eng = Engram(root=w.cfg.root, read_only=read_only)
    assert eng._store_mode == "production"
    assert eng.get_lessons(_update_access=False) == w.expected_lessons
    assert eng.get_profile() == w.expected_profile
    assert isinstance(eng.generate_context_report(level="quick"), tuple)
    assert not carries_replay_marker(eng.generate_context(level="quick"))
    assert (w.cfg.root / MARKER).read_bytes() == marker_before
    assert (w.cfg.receipts_dir / RECEIPTS_FILE).read_bytes() == ledger_before
    assert not (w.cfg.root.with_name(w.cfg.root.name + "_guard") / "refusals.jsonl").exists()
    if read_only:
        assert _snap(w.cfg.root) == before
    assert IsolatedStore.open(w.cfg).mode == "production"


def test_legacy_production_cannot_be_attached_as_replay(legacy_production_root):
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=legacy_production_root.cfg.root, read_only=True, store_mode=MODE)


def test_structured_export_keeps_scalar_reference_lists():
    from piia_engram.isolated_store import mark_replay_export

    payload = {"ids": ["sample-reference"], "references": ("sample-reference",),
               "entries": [{"id": "sample-reference", "summary": "generic observation"}]}
    marked = mark_replay_export(payload)
    assert carries_replay_marker(marked)
    assert marked["ids"] == payload["ids"]
    assert marked["references"] == payload["references"]
    assert marked["entries"][0]["id"] in marked["ids"]
