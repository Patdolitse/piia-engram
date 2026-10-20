"""Regressions for the maintenance review, using synthetic local stores only."""

import json
import sqlite3

import pytest

from piia_engram import sensitivity as sv
from piia_engram.core import Engram
from piia_engram.isolated_store import (
    CONFIG_ENV, LIMITS_FILE, MARKER, REPLAY_EXPORT_MARKER, REPLAY_MARKER_FILE,
    Config, GuardRefused, init_root,
)
from piia_engram.isolated_store_launch import build_child_env
from test_isolated_store import _make_world, _snap
from test_replay_experience import MODE, _admit


@pytest.mark.parametrize("field,text", [
    ("summary", "Call me at tel13800138000"),
    ("fingerprint", "Contact: 13800138000_mobile"),
])
def test_review_1_free_text_phone_stays_private(field, text):
    from piia_engram.governance import gate

    item = {"sensitivity": "public", field: text}
    assert sv.classify_item(item) == "private"
    assert gate(sv.annotate_items([item]), "read-only-external")[0] == []


@pytest.mark.parametrize("field", ["fingerprint", "sha256", "content_sha256", "content_hash", "query_digest"])
def test_review_2_digest_fields_keep_card_detection(field):
    digest = "a" * 23 + "4111111111111111" + "b" * 25
    assert len(digest) == 64
    assert sv.classify_item({"sensitivity": "public", field: digest}) == "private"
    assert sv.classify_item({"sensitivity": "public", "correction_approval": {field: digest}}) == "private"


def custom_replay(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, init=False)
    w.data.update(mode=MODE, receipts_dir=str(w.area / "custom_ledger"))
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    w.cfg = Config.load(w.cfg_path)
    for key, value in build_child_env({}, w.cfg).items():
        monkeypatch.setenv(key, value)
    w.pr = init_root(w.cfg)
    assert _admit(w)["result"] == "admitted"
    for name in (MARKER, LIMITS_FILE, REPLAY_MARKER_FILE):
        (w.pr.root / name).unlink()
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    return w


def test_review_3_custom_external_ledger_cannot_attach(tmp_path, monkeypatch):
    w = custom_replay(tmp_path, monkeypatch)
    ledger = w.pr.receipts_path.read_bytes()
    with pytest.raises(GuardRefused, match="guard_mode_immutable.*doctor"):
        Engram(root=w.pr.root, read_only=True)
    assert w.pr.receipts_path.read_bytes() == ledger


def test_review_4_same_size_middle_archive_edit_refuses_cached_read(tmp_path):
    eng = Engram(root=tmp_path / "ordinary")
    path = eng.root / "knowledge/overflow_archive/lessons.jsonl"
    path.parent.mkdir(exist_ok=True)
    padding = {"id": "padding", "summary": "p" * 9000}
    middle = {"id": "middle", "summary": "ARCHIVE_CANARY", "store_mode": "production_______"}
    original = "\n".join(json.dumps(row) for row in (padding, middle, padding)) + "\n"
    path.write_text(original, encoding="utf-8")
    assert eng.get_overflow_archived("lesson", "middle")["summary"] == "ARCHIVE_CANARY"
    changed = original.replace("production_______", MODE)
    assert len(changed) == len(original)
    assert changed[:4096] == original[:4096] and changed[-4096:] == original[-4096:]
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        eng.get_overflow_archived("lesson", "middle")
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=eng.root, read_only=True)


def contaminated(root):
    eng = Engram(root=root)
    eng.add_lesson("DERIVED_CANARY synthetic fixture", source_tool="cli")
    eng.refresh_quick_context()
    assert eng.rebuild_index()["indexed"] == 1
    path = root / "knowledge/lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["store_mode"] = MODE
    path.write_text(json.dumps(rows), encoding="utf-8")
    return eng, path, rows[0]


def test_review_5_refusal_then_repair_invalidates_existing_artifacts(tmp_path, monkeypatch):
    eng, path, marked = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    original = path.read_bytes()
    for read_only in (True, False):
        with pytest.raises(GuardRefused, match="guard_mode_immutable.*doctor"):
            Engram(root=eng.root, read_only=read_only)
    from piia_engram.replay_row_repair import run_cli
    assert run_cli(["replay-rows", "--apply"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == []
    assert not (eng.root / "quick_context.md").exists()
    assert not (eng.root / "search_index.db").exists()
    recovery = eng.root / "recovery/replay-rows"
    assert any(p.read_bytes() == original for p in recovery.rglob("lessons.json.bak"))
    quarantine = next(recovery.rglob("quarantine.jsonl"))
    assert json.loads(quarantine.read_text(encoding="utf-8"))["row"] == marked
    audit = (eng.root / "audit.log").read_text(encoding="utf-8")
    assert "replay_rows_repaired" in audit and "DERIVED_CANARY" not in audit
    clean = Engram(root=eng.root)
    clean.refresh_quick_context()
    clean.rebuild_index()
    assert "DERIVED_CANARY" not in (eng.root / "quick_context.md").read_text(encoding="utf-8")
    with sqlite3.connect(eng.root / "search_index.db") as connection:
        assert connection.execute("SELECT eid, doc FROM fts").fetchall() == []


def test_doctor_and_repair_dry_run_are_metadata_only_zero_write(tmp_path, monkeypatch, capsys):
    from piia_engram import doctor
    from piia_engram.replay_row_repair import run_cli

    eng, _, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    before = _snap(tmp_path)
    assert doctor._run_functional_checks() > 0
    text = capsys.readouterr().out
    assert "knowledge/lessons.json: 1" in text and "DERIVED_CANARY" not in text
    assert doctor.run_doctor_json() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stored_replay"]["files"] == {"knowledge/lessons.json": 1}
    assert "repair replay-rows" in report["stored_replay"]["hint"]
    assert run_cli(["replay-rows", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "knowledge/lessons.json" in output and "DERIVED_CANARY" not in output
    assert _snap(tmp_path) == before


def test_repair_refuses_custom_replay_ledger_with_lost_metadata(tmp_path, monkeypatch):
    from piia_engram.replay_row_repair import run_cli

    w = custom_replay(tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_DIR", str(w.pr.root))
    before = _snap(tmp_path)
    assert run_cli(["replay-rows", "--apply"]) == 1
    assert _snap(tmp_path) == before


@pytest.mark.parametrize("bad", [b"\xff", b"{torn"])
def test_repair_never_rewrites_when_any_knowledge_file_is_undecodable(tmp_path, monkeypatch, bad):
    from piia_engram.replay_row_repair import run_cli

    eng, _, _ = contaminated(tmp_path / "ordinary")
    (eng.root / "knowledge/decisions.json").write_bytes(bad)
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    before = _snap(tmp_path)
    assert run_cli(["replay-rows", "--apply"]) == 1
    assert _snap(tmp_path) == before


@pytest.mark.parametrize("field", ["fingerprint", "sha256", "content_sha256", "content_hash", "query_digest"])
@pytest.mark.parametrize("upper", [False, True])
def test_exact_digest_phone_exception_is_field_scoped(field, upper):
    digest = "a" * 25 + "13800138000" + "b" * 28
    if upper:
        digest = digest.upper()
    assert sv.classify_item({"sensitivity": "public", field: digest}) == "public"
    assert sv.classify_item({"sensitivity": "public", "summary": digest}) == "private"
    for malformed in ("tel13800138000", "13800138000_mobile", digest[1:],
                      digest.replace("a", "ａ").replace("A", "Ａ"), " " + digest[:-1],
                      "owner@example.com", "a11010519491231002Xb"):
        assert sv.classify_item({"sensitivity": "public", field: malformed}) == sv.classify_value(malformed)


@pytest.mark.parametrize("relative", ["knowledge/lessons.json", "knowledge/decisions.json",
                                     "knowledge/overflow_archive/lessons.jsonl",
                                     "knowledge/overflow_archive/decisions.jsonl"])
def test_repair_keeps_clean_rows_and_markers_in_backups_and_quarantine(tmp_path, monkeypatch, relative):
    from piia_engram.replay_row_repair import run_cli

    root = tmp_path / "ordinary"
    Engram(root=root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    path = root / relative
    path.parent.mkdir(exist_ok=True)
    clean = {"id": "clean", "summary": "Keep this exact clean row"}
    marked = {"id": "pasted", "detail": "LOCAL_CANARY" + REPLAY_EXPORT_MARKER}
    text = (json.dumps(clean) + "\r\n" + json.dumps(marked) + "\r\n"
            if path.suffix == ".jsonl" else json.dumps([clean, marked]))
    path.write_bytes(text.encode("utf-8"))
    original = path.read_bytes()
    assert run_cli(["replay-rows", "--apply"]) == 0
    actual = ([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
              if path.suffix == ".jsonl" else json.loads(path.read_text(encoding="utf-8")))
    assert actual == [clean]
    assert any(p.read_bytes() == original for p in (root / "recovery").rglob("*.bak"))
    quarantine = next((root / "recovery").rglob("quarantine.jsonl"))
    assert json.loads(quarantine.read_text(encoding="utf-8")) == {"file": relative, "row": marked}
    assert Engram(root=root, read_only=True)._store_mode == "production"
    before = _snap(tmp_path)
    assert run_cli(["replay-rows", "--apply"]) == 0
    assert _snap(tmp_path) == before


@pytest.mark.parametrize("signal", [MARKER, LIMITS_FILE, REPLAY_MARKER_FILE])
def test_repair_refuses_root_metadata_without_writes(tmp_path, monkeypatch, signal):
    from piia_engram.replay_row_repair import run_cli

    eng, _, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    (eng.root / signal).write_text("{}", encoding="utf-8")
    before = _snap(tmp_path)
    assert run_cli(["replay-rows", "--apply"]) == 1
    assert _snap(tmp_path) == before


def test_repair_cannot_run_with_mcp_origin(tmp_path, monkeypatch):
    from piia_engram.replay_row_repair import run_cli
    from piia_engram.write_provenance import origin_scope

    eng, _, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    before = _snap(tmp_path)
    with origin_scope("mcp"):
        assert run_cli(["replay-rows", "--apply"]) == 1
    assert _snap(tmp_path) == before


def test_repair_invalidates_named_generated_exports_and_custom_snapshot(tmp_path, monkeypatch):
    from piia_engram.replay_row_repair import run_cli

    eng, _, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    generated = [eng.root / "exports/knowledge_report_2026-01-01.md",
                 eng.root / "exports/review_2026-01-01.html"]
    custom = eng.root / "custom/cold-start.md"
    custom.parent.mkdir()
    custom.write_bytes((eng.root / "quick_context.md").read_bytes())
    for path in generated:
        path.write_text("DERIVED_CANARY", encoding="utf-8")
    assert run_cli(["replay-rows", "--apply"]) == 0
    assert not any(path.exists() for path in [*generated, custom])


def test_interrupted_repair_stays_closed_and_can_finish(tmp_path, monkeypatch):
    from piia_engram import replay_row_repair as repair

    eng, path, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    original = path.read_bytes()
    replace = repair.replace_with_retry
    monkeypatch.setattr(repair, "replace_with_retry", lambda *args: (_ for _ in ()).throw(OSError()))
    assert repair.run_cli(["replay-rows", "--apply"]) == 1
    assert path.read_bytes() == original
    assert (eng.root / repair.PENDING).exists()
    with pytest.raises(GuardRefused):
        Engram(root=eng.root, read_only=True)
    monkeypatch.setattr(repair, "replace_with_retry", replace)
    assert repair.run_cli(["replay-rows", "--apply"]) == 0
    assert not (eng.root / repair.PENDING).exists()
    assert Engram(root=eng.root, read_only=True).get_lessons() == []


def test_cli_repair_dispatches_before_startup_effects(tmp_path, monkeypatch):
    import sys
    from piia_engram import setup_wizard as cli

    eng, _, _ = contaminated(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    monkeypatch.setattr(sys, "argv", ["engram", "repair", "replay-rows", "--dry-run"])
    monkeypatch.setattr(cli, "_skips_startup_effects", lambda *args: pytest.fail("startup effect"))
    before = _snap(tmp_path)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert _snap(tmp_path) == before
