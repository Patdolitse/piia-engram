"""Tests for the zero-write resume entry (``engram dock-resume``).

A local desktop client must be able to read a resume brief without mutating the
store. These pin two things: (1) ``Engram(read_only=True)`` makes ZERO writes to
the store root (no session stamp / audit / migration / structure creation), and
(2) the ``dock-resume`` CLI emits a paste-ready brief / JSON and honors
``ENGRAM_DIR``. The read_only flag is additive — the normal init path is asserted
unchanged so existing behavior can't regress.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Suite isolation: never touch the real ~/.engram, keep audit/fragmentation
    # noise out of temp roots (same carve-out the rest of the suite uses).
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)


def _snapshot(root: Path) -> dict[str, tuple]:
    """Snapshot every file (mtime + sha256) AND every directory, so even an
    empty directory created under read_only is caught. Absent root -> empty."""
    out: dict[str, tuple] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_dir():
            out[rel + "/"] = ("<dir>",)
        elif p.is_file():
            out[rel] = (p.stat().st_mtime_ns,
                        hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _populate(root: Path):
    """A normally-initialized store (structure + session stamp written)."""
    from piia_engram.core import Engram

    return Engram(root=root)


# --- core read_only flag: guaranteed zero writes ---------------------------


def test_read_only_open_makes_zero_writes(tmp_path):
    from piia_engram.core import Engram

    store = tmp_path / "store"
    _populate(store)  # normal init writes structure + session_state
    before = _snapshot(store)

    eng = Engram(root=store, read_only=True)
    brief = eng.get_resume_brief(project_folder="", token_budget=800)

    assert _snapshot(store) == before  # not one byte changed
    assert eng._read_only is True
    assert isinstance(brief, dict) and "markdown" in brief


def test_read_only_does_not_create_missing_store(tmp_path):
    from piia_engram.core import Engram

    store = tmp_path / "absent"
    Engram(root=store, read_only=True)  # opening read-only must not mkdir
    assert not store.exists()


def test_default_init_still_writes_structure(tmp_path):
    """Guard: read_only is additive — the normal path is unchanged."""
    from piia_engram.core import Engram

    store = tmp_path / "normal"
    Engram(root=store)
    assert (store / "schema_version.json").exists()
    assert (store / "session_state.json").exists()


# --- dock-resume CLI --------------------------------------------------------


def _cli(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_resume

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_resume(argv)


def test_dock_resume_help(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-resume" in capsys.readouterr().out


def test_dock_resume_text_is_paste_ready(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli(monkeypatch, tmp_path, []) == 0
    assert capsys.readouterr().out.strip()  # non-empty markdown body


def test_dock_resume_json_is_structured_and_read_only(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["engram_dir"] == str(tmp_path / "store")
    assert "markdown" in payload


def test_dock_resume_cli_is_zero_write_end_to_end(monkeypatch, tmp_path):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    _cli(monkeypatch, tmp_path, ["--json"])
    assert _snapshot(store) == before  # the whole CLI path wrote nothing


def test_dock_resume_rejects_bad_budget(monkeypatch, tmp_path, capsys):
    assert _cli(monkeypatch, tmp_path, ["--budget", "x"]) == 2
    assert "budget" in capsys.readouterr().out.lower()


# --- crypto / audit / real entry / arg edges ----------


def test_read_only_zero_write_with_encryption_no_salt(tmp_path, monkeypatch):
    """ENGRAM_SECRET on + no .corpus_salt: read_only must NOT mkdir or mint salt."""
    from piia_engram.core import Engram

    monkeypatch.setenv("ENGRAM_SECRET", "test-secret-xyz")
    store = tmp_path / "absent_enc"
    Engram(root=store, read_only=True)
    assert not store.exists()


def test_read_only_zero_write_on_encrypted_store(tmp_path, monkeypatch):
    from piia_engram.core import Engram

    monkeypatch.setenv("ENGRAM_SECRET", "test-secret-xyz")
    store = tmp_path / "enc_store"
    Engram(root=store)  # normal init under encryption (mints salt + structure)
    before = _snapshot(store)
    eng = Engram(root=store, read_only=True)
    eng.get_resume_brief(project_folder="", token_budget=400)
    assert _snapshot(store) == before


def test_read_only_zero_write_with_audit_forced_on(tmp_path, monkeypatch):
    from piia_engram.core import Engram

    monkeypatch.delenv("ENGRAM_TEST", raising=False)  # let audit follow its env
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    store = tmp_path / "audit_store"
    Engram(root=store)  # normal init with audit on
    before = _snapshot(store)
    eng = Engram(root=store, read_only=True)
    eng.get_resume_brief(project_folder="", token_budget=400)
    assert _snapshot(store) == before


def test_real_main_entry_is_zero_write(tmp_path, monkeypatch):
    """The console entry (setup_wizard.main) must be zero-write too — the update
    reminder would otherwise write .update_check.json into the store."""
    from piia_engram import setup_wizard

    store = tmp_path / "store"
    _populate(store)
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    monkeypatch.setattr("sys.argv", ["engram", "dock-resume", "--json"])
    before = _snapshot(store)
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    assert exc.value.code == 0
    assert _snapshot(store) == before
    assert not (store / ".update_check.json").exists()


def test_dock_resume_budget_must_be_positive(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--budget", "0"]) == 2
    assert _cli(monkeypatch, tmp_path, ["--budget", "-5"]) == 2


def test_dock_resume_budget_requires_value(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--budget"]) == 2


def test_dock_resume_project_requires_value(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--project"]) == 2


def test_dock_resume_rejects_unknown_option(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--bogus"]) == 2


# --- flag-like values + reminder sentinel -------------


def test_dock_resume_project_value_cannot_be_a_flag(monkeypatch, tmp_path):
    # `--project --json` must NOT swallow the flag as the project value.
    assert _cli(monkeypatch, tmp_path, ["--project", "--json"]) == 2
    assert _cli(monkeypatch, tmp_path, ["--project", "--bogus"]) == 2


def test_dock_resume_budget_value_cannot_be_a_flag(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--budget", "--json"]) == 2


# --- dock-search CLI + read_only migration zero-write ------


def _cli_search(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_search

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_search(argv)


def test_dock_search_help(monkeypatch, tmp_path):
    assert _cli_search(monkeypatch, tmp_path, ["--help"]) == 0


def test_dock_search_requires_nonempty_query(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_search(monkeypatch, tmp_path, ["--json"]) == 2  # no --query


def test_dock_search_rejects_bad_limit_and_scope(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_search(monkeypatch, tmp_path, ["--query", "x", "--limit", "0"]) == 2
    assert _cli_search(monkeypatch, tmp_path, ["--query", "x", "--scope", "bogus"]) == 2


def test_dock_search_query_value_cannot_be_a_flag(monkeypatch, tmp_path):
    assert _cli_search(monkeypatch, tmp_path, ["--query", "--json"]) == 2


def test_dock_search_json_is_structured_and_zero_write(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    assert _cli_search(monkeypatch, tmp_path, ["--query", "anything", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert isinstance(payload["results"], list)
    assert _snapshot(store) == before  # the whole search path wrote nothing


def test_read_only_does_not_persist_field_migration(tmp_path):
    """A legacy entry missing backfilled fields must be migrated in MEMORY only
    under read_only — never rewritten to disk."""
    from piia_engram.core import Engram

    store = tmp_path / "store"
    eng = Engram(root=store)  # normal init builds structure
    # A legacy lesson missing fields that _ensure_fields backfills.
    lessons = eng._knowledge_dir / "lessons.json"
    lessons.write_text(
        json.dumps([{"summary": "legacy lesson", "id": "legacy1"}]),
        encoding="utf-8",
    )
    before = _snapshot(store)

    ro = Engram(root=store, read_only=True)
    items = ro._read_entries(lessons, "lesson")

    # The in-memory entry IS backfilled (proving it would normally be rewritten)…
    assert items and items[0].get("summary") == "legacy lesson"
    assert "status" in items[0] or "tier" in items[0]
    # …but the file on disk is byte-for-byte unchanged under read_only.
    assert _snapshot(store) == before


# --- dock-export CLI (full backup; deliberate write, JSON contract) ----------


def _cli_export(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_export

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_export(argv)


def test_dock_export_help(monkeypatch, tmp_path):
    assert _cli_export(monkeypatch, tmp_path, ["--help"]) == 0


def test_dock_export_json_success_writes_a_backup(monkeypatch, tmp_path, capsys):
    import os

    _populate(tmp_path / "store")
    assert _cli_export(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["path"]
    assert os.path.exists(payload["path"])  # the backup file was produced


def test_dock_export_json_arg_error_stays_json(monkeypatch, tmp_path, capsys):
    # The --json contract must hold even on arg-parse errors.
    assert _cli_export(monkeypatch, tmp_path, ["--json", "--bogus"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_dock_export_missing_output_value(monkeypatch, tmp_path):
    assert _cli_export(monkeypatch, tmp_path, ["--output"]) == 2


# --- dock-portrait CLI (zero-write user portrait) ----------------------------


def _cli_portrait(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_portrait

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_portrait(argv)


def test_dock_portrait_help(monkeypatch, tmp_path):
    assert _cli_portrait(monkeypatch, tmp_path, ["--help"]) == 0


def test_dock_portrait_json_is_structured_and_zero_write(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    assert _cli_portrait(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert "markdown" in payload
    assert _snapshot(store) == before  # builds in memory; never saves a snapshot


def test_dock_portrait_rejects_unknown_option(monkeypatch, tmp_path):
    assert _cli_portrait(monkeypatch, tmp_path, ["--bogus"]) == 2


def test_dock_portrait_json_arg_error_stays_json(monkeypatch, tmp_path, capsys):
    # The --json contract holds even on arg errors.
    assert _cli_portrait(monkeypatch, tmp_path, ["--json", "--bogus"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["markdown"] == ""


def test_dock_portrait_html_writes_styled_page_zero_write(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_portrait

    store = tmp_path / "store"
    _populate(store)
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    out = tmp_path / "portrait.html"
    before = _snapshot(store)
    assert _run_dock_portrait(["--html", "--output", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert 'class="hero"' in page and 'class="stats"' in page
    assert _snapshot(store) == before  # rendering HTML never touches the store


def test_dock_portrait_html_requires_output(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_portrait

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_portrait(["--html"]) == 2


def test_dock_portrait_html_zero_write_with_missing_trust_boundaries(monkeypatch, tmp_path):
    """read_only must NOT lazily backfill trust_boundaries.json to disk — the
    central _atomic_write guard."""
    from piia_engram.setup_wizard import _run_dock_portrait

    store = tmp_path / "store"
    _populate(store)
    tb = store / "identity" / "trust_boundaries.json"
    if tb.exists():
        tb.unlink()  # simulate a legacy/missing store that would trigger backfill
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    out = tmp_path / "p.html"
    before = _snapshot(store)
    assert _run_dock_portrait(["--html", "--output", str(out)]) == 0
    assert out.exists()  # the portrait still renders (backfilled in memory)
    assert _snapshot(store) == before  # …but trust_boundaries.json was not written


# --- dock-archive / dock-restore / dock-archived (reversible prune) -----------


def _seed_lesson(tmp_path, summary="archive-me test lesson"):
    from piia_engram.core import Engram

    e = Engram(root=tmp_path / "store")
    r = e.add_lesson(summary)
    return (r or {}).get("id", "")


def test_dock_archive_restore_roundtrip(monkeypatch, tmp_path, capsys):
    from piia_engram.setup_wizard import (
        _run_dock_archive,
        _run_dock_archived,
        _run_dock_restore,
    )

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    lid = _seed_lesson(tmp_path)
    assert lid

    # Archive a verified entry (owner-confirmed → allowed, reversible).
    assert _run_dock_archive(["--id", lid, "--json"]) == 0
    archived = json.loads(capsys.readouterr().out)
    assert archived["ok"] is True
    assert archived["result"]["to_tier"] == "archived"

    # It shows up in the archived list (zero-write).
    assert _run_dock_archived(["--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["ok"] is True and listed["read_only"] is True
    assert any(r["id"] == lid for r in listed["results"])

    # Restore moves it back to its prior tier (verified).
    assert _run_dock_restore(["--id", lid, "--json"]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["ok"] is True
    assert restored["result"]["to_tier"] == "verified"

    # …and it's gone from the archived list.
    assert _run_dock_archived(["--json"]) == 0
    after = json.loads(capsys.readouterr().out)
    assert all(r["id"] != lid for r in after["results"])


def test_dock_archived_is_zero_write(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_archived

    store = tmp_path / "store"
    _populate(store)
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    before = _snapshot(store)
    assert _run_dock_archived(["--json"]) == 0
    assert _snapshot(store) == before


def test_dock_archive_requires_id(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_archive

    _populate(tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_archive(["--json"]) == 2


def test_dock_archive_not_found_is_json_error(monkeypatch, tmp_path, capsys):
    from piia_engram.setup_wizard import _run_dock_archive

    _populate(tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_archive(["--id", "nope", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_dock_restore_requires_id(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_restore

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_restore(["--json"]) == 2


def test_dock_restore_not_found_is_json_error(monkeypatch, tmp_path, capsys):
    from piia_engram.setup_wizard import _run_dock_restore

    _populate(tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_restore(["--id", "nope", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_dock_restore_rejects_unknown_option(monkeypatch, tmp_path):
    from piia_engram.setup_wizard import _run_dock_restore

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_restore(["--json", "--bogus"]) == 2


def test_dock_archived_json_arg_error_stays_json(monkeypatch, tmp_path, capsys):
    from piia_engram.setup_wizard import _run_dock_archived

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert _run_dock_archived(["--json", "--bogus"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_dock_resume_main_skips_update_reminder(tmp_path, monkeypatch):
    """Definitively prove the zero-write entry never invokes the update reminder
    (which would write .update_check.json) — via a sentinel, not just a snapshot."""
    import piia_engram.update_check as uc
    from piia_engram import setup_wizard

    called: list[int] = []
    monkeypatch.setattr(uc, "maybe_print_update_notice",
                        lambda *a, **k: called.append(1))
    store = tmp_path / "store"
    _populate(store)
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    monkeypatch.setattr("sys.argv", ["engram", "dock-resume", "--json"])
    with pytest.raises(SystemExit):
        setup_wizard.main()
    assert called == []  # reminder never invoked for dock-resume


# --- dock-onboard-scan / dock-onboard-commit (D2 onboarding) ----------------


_ONBOARD_SAMPLE = (
    "我们决定使用 PostgreSQL 而不是 MySQL。\n"
    "必须始终验证用户输入，避免注入攻击。\n"
    "今天天气不错。\n"
)


def _scan(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_onboard_scan

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_onboard_scan(argv)


def _commit(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_onboard_commit

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_onboard_commit(argv)


def test_dock_onboard_scan_help(monkeypatch, tmp_path, capsys):
    assert _scan(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-onboard-scan" in capsys.readouterr().out


def test_dock_onboard_scan_text_json_and_zero_write(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    assert _scan(monkeypatch, tmp_path, ["--text", _ONBOARD_SAMPLE, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["read_only"] is True
    assert data["count"] >= 1
    assert all("type" in c and "text" in c for c in data["candidates"])
    assert _snapshot(store) == before  # dry-run preview: not one byte changed


def test_dock_onboard_scan_unknown_arg_is_json(monkeypatch, tmp_path, capsys):
    assert _scan(monkeypatch, tmp_path, ["--bogus", "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "unknown option" in data["error"]


def test_dock_onboard_scan_empty_input_errors(monkeypatch, tmp_path, capsys):
    assert _scan(monkeypatch, tmp_path, ["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_scan_dedupes_candidates(monkeypatch, tmp_path, capsys):
    dup = ("我们决定使用 PostgreSQL 而不是 MySQL。\n"
           "我们决定使用 PostgreSQL 而不是 MySQL。\n")
    assert _scan(monkeypatch, tmp_path, ["--text", dup, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1  # identical sentence collapsed


def test_dock_onboard_scan_text_file(monkeypatch, tmp_path, capsys):
    f = tmp_path / "notes.txt"
    f.write_text(_ONBOARD_SAMPLE, encoding="utf-8")
    assert _scan(monkeypatch, tmp_path, ["--text-file", str(f), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and "text-file" in data["sources"]
    assert data["count"] >= 1


def test_dock_onboard_scan_folder_readme(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "README.md").write_text(
        "我们决定使用 PostgreSQL 而不是 MySQL。\n"
        "必须始终在上线前测试备份恢复，避免数据丢失。\n",
        encoding="utf-8",
    )
    assert _scan(monkeypatch, tmp_path, ["--folder", str(proj), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and "readme" in data["sources"]
    assert data["count"] >= 1


def test_dock_onboard_scan_missing_folder_degrades(monkeypatch, tmp_path, capsys):
    # non-existent folder and no text -> clean error, never crashes
    assert _scan(monkeypatch, tmp_path,
                 ["--folder", str(tmp_path / "nope"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_commit_help(monkeypatch, tmp_path, capsys):
    assert _commit(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-onboard-commit" in capsys.readouterr().out


def test_dock_onboard_commit_writes_knowledge(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    cands = [
        {"type": "decision",
         "text": "我们决定使用 PostgreSQL 而不是 MySQL", "domain": "database"},
        {"type": "lesson",
         "text": "必须始终验证用户输入，避免注入攻击", "domain": ""},
    ]
    cf = tmp_path / "cands.json"
    cf.write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)["result"]
    assert res["saved_lessons"] == 1 and res["saved_decisions"] == 1

    from piia_engram.core import Engram

    eng = Engram(root=store, read_only=True)
    les = eng.get_lessons(limit=None, _update_access=False, _migrate_fields=False)
    dec = eng.get_decisions(limit=None, _update_access=False, _migrate_fields=False)
    assert len(les) >= 1 and len(dec) >= 1


def test_dock_onboard_commit_requires_file(monkeypatch, tmp_path, capsys):
    assert _commit(monkeypatch, tmp_path, ["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_commit_rejects_non_array(monkeypatch, tmp_path, capsys):
    cf = tmp_path / "bad.json"
    cf.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_commit_empty_array_errors(monkeypatch, tmp_path, capsys):
    cf = tmp_path / "empty.json"
    cf.write_text("[]", encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_round_trip(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    assert _scan(monkeypatch, tmp_path, ["--text", _ONBOARD_SAMPLE, "--json"]) == 0
    scan = json.loads(capsys.readouterr().out)
    assert scan["count"] >= 1
    cf = tmp_path / "rt.json"
    cf.write_text(json.dumps(scan["candidates"], ensure_ascii=False),
                  encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)["result"]
    assert (res["saved_lessons"] + res["saved_decisions"]) == scan["count"]


def test_dock_onboard_scan_main_skips_update_reminder(tmp_path, monkeypatch):
    """dock-onboard-scan is a zero-write entry — it must NOT invoke the update
    reminder (which would write .update_check.json into the store)."""
    import piia_engram.update_check as uc
    from piia_engram import setup_wizard

    called: list[int] = []
    monkeypatch.setattr(uc, "maybe_print_update_notice",
                        lambda *a, **k: called.append(1))
    store = tmp_path / "store"
    _populate(store)
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    monkeypatch.setattr("sys.argv",
                        ["engram", "dock-onboard-scan",
                         "--text", _ONBOARD_SAMPLE, "--json"])
    with pytest.raises(SystemExit):
        setup_wizard.main()
    assert called == []  # reminder never invoked for the zero-write scan


def test_dock_onboard_commit_floors_near_empty(monkeypatch, tmp_path, capsys):
    """Near-empty candidate text is skipped by the floor. Owner-confirmed content is
    otherwise trusted — commit no longer applies the trigger-word gate (that stays
    scan-side), so the only commit-time bar is non-empty + minimum length."""
    store = tmp_path / "store"
    _populate(store)
    cf = tmp_path / "lowq.json"
    cf.write_text(json.dumps([{"type": "lesson", "text": "嗯", "domain": "",
                               "quality_score": 0.99}], ensure_ascii=False),
                  encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)["result"]
    assert res["saved_lessons"] == 0 and res["saved_decisions"] == 0
    assert res["skipped"] >= 1


def test_dock_onboard_commit_handles_malformed_entries(monkeypatch, tmp_path, capsys):
    """Non-dict / non-string text / non-string domain are skipped without crashing;
    a valid entry in the same array is still written (no partial-write-then-throw)."""
    store = tmp_path / "store"
    _populate(store)
    cands = [
        "not a dict",
        {"type": "lesson", "text": 12345, "domain": ""},          # non-string text
        {"type": "lesson", "text": "", "domain": ""},               # empty text
        {"type": "decision", "text": "我们决定使用 PostgreSQL 而不是 MySQL",
         "domain": ["not", "a", "string"]},                         # bad domain, valid text
    ]
    cf = tmp_path / "mixed.json"
    cf.write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)["result"]
    assert res["saved_decisions"] == 1   # the one valid entry still written
    assert res["skipped"] >= 3           # the rest skipped, no crash


def test_dock_onboard_scan_text_requires_value(monkeypatch, tmp_path, capsys):
    """`--text --json` must be a missing-value error, not swallow the --json flag."""
    assert _scan(monkeypatch, tmp_path, ["--text", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_onboard_commit_accepts_edited_no_trigger_description(
        monkeypatch, tmp_path, capsys):
    """An owner-edited detailed description with NO auto-trigger words is still
    written — commit trusts confirmed content; the trigger gate is scan-side only."""
    store = tmp_path / "store"
    _populate(store)
    detailed = ("这是一段手动补充的详细描述，没有任何决定或教训触发词，"
                "纯陈述性内容，但 owner 确认要保存")
    cf = tmp_path / "edited.json"
    cf.write_text(json.dumps([{"type": "lesson", "text": detailed, "domain": ""}],
                             ensure_ascii=False), encoding="utf-8")
    assert _commit(monkeypatch, tmp_path,
                   ["--candidates-file", str(cf), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)["result"]
    assert res["saved_lessons"] == 1


# --- dock-update (edit an entry's content) ----------------------------------


def _update(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_update

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_update(argv)


def test_dock_update_help(monkeypatch, tmp_path, capsys):
    assert _update(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-update" in capsys.readouterr().out


def test_dock_update_edits_lesson_and_persists(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    eng = _populate(store)
    lid = eng.add_lesson({"summary": "原始经验摘要"}).get("id")
    uf = tmp_path / "u.json"
    uf.write_text(json.dumps({"summary": "改过的更详细的经验摘要"}, ensure_ascii=False),
                  encoding="utf-8")
    assert _update(monkeypatch, tmp_path,
                   ["--id", lid, "--updates-file", str(uf), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    from piia_engram.core import Engram

    eng2 = Engram(root=store, read_only=True)
    les = eng2.get_lessons(limit=None, _update_access=False, _migrate_fields=False)
    assert any(l.get("id") == lid and l.get("summary") == "改过的更详细的经验摘要"
               for l in les)


def test_dock_update_requires_id_and_file(monkeypatch, tmp_path, capsys):
    assert _update(monkeypatch, tmp_path, ["--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert _update(monkeypatch, tmp_path, ["--id", "x", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_update_rejects_blank_primary(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    eng = _populate(store)
    lid = eng.add_lesson({"summary": "原始"}).get("id")
    uf = tmp_path / "u.json"
    uf.write_text(json.dumps({"summary": "   "}), encoding="utf-8")
    assert _update(monkeypatch, tmp_path,
                   ["--id", lid, "--updates-file", str(uf), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_update_not_found_is_json_error(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    uf = tmp_path / "u.json"
    uf.write_text(json.dumps({"summary": "x"}), encoding="utf-8")
    assert _update(monkeypatch, tmp_path,
                   ["--id", "nonexistent_id", "--updates-file", str(uf), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_search_returns_editable_fields(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    eng = _populate(store)
    eng.add_lesson({"summary": "PostgreSQL 数据库选型经验", "detail": "细节内容"})
    assert _cli_search(monkeypatch, tmp_path, ["--query", "PostgreSQL", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    hit = next((r for r in data["results"] if r["kind"] == "lesson"), None)
    assert hit and isinstance(hit.get("fields"), dict)
    assert "summary" in hit["fields"] and "detail" in hit["fields"]
    assert _snapshot(store)  # search stays read-only; presence check only


def test_dock_title_only_decision_search_fallback_and_update_sync(
        monkeypatch, tmp_path, capsys):
    """Extraction-written decisions hold primary text in `title` (question is null).
    Search must fall back to title for display + fields.question; editing question
    must persist BOTH question and the legacy title (no stale identity)."""
    store = tmp_path / "store"
    eng = _populate(store)
    did = eng.add_decision({"title": "采用方案X作为长期架构", "choice": ""}).get("id")

    # search shows it (not "(decision)") and exposes it as fields.question
    assert _cli_search(monkeypatch, tmp_path, ["--query", "方案X", "--json"]) == 0
    hit = next((r for r in json.loads(capsys.readouterr().out)["results"]
                if r["kind"] == "decision"), None)
    assert hit and hit["title"] != "(decision)"
    assert hit["fields"]["question"] == "采用方案X作为长期架构"

    # editing question persists BOTH question and the legacy title
    uf = tmp_path / "u.json"
    uf.write_text(json.dumps({"question": "改为采用方案Y"}, ensure_ascii=False),
                  encoding="utf-8")
    assert _update(monkeypatch, tmp_path,
                   ["--id", did, "--updates-file", str(uf), "--json"]) == 0
    from piia_engram.core import Engram

    eng2 = Engram(root=store, read_only=True)
    decs = eng2.get_decisions(limit=None, _update_access=False, _migrate_fields=False)
    d = next((x for x in decs if x.get("id") == did), None)
    assert d.get("question") == "改为采用方案Y"
    assert d.get("title") == "改为采用方案Y"  # legacy title synced, not stale


# --- dock-list: zero-write list of all active entries (我的记忆 browse view) --


def _cli_list(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_list

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_list(argv)


def test_dock_list_help(monkeypatch, tmp_path, capsys):
    assert _cli_list(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-list" in capsys.readouterr().out


def test_dock_list_empty_json_is_read_only(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli_list(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["count"] == 0
    assert payload["results"] == []


def test_dock_list_returns_active_lessons_and_decisions(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    eng = _populate(store)
    eng.add_lesson({"summary": "用 portalocker 做跨平台文件锁", "detail": "细节说明"})
    eng.add_decision({"question": "存储用 JSON 还是 SQLite", "choice": "JSON 优先",
                      "reasoning": "可读可编辑可覆盖"})
    assert _cli_list(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert {r["kind"] for r in payload["results"]} == {"lesson", "decision"}
    for r in payload["results"]:
        assert r["id"] and r["title"] and "fields" in r and "copy" in r
    dec = next(r for r in payload["results"] if r["kind"] == "decision")
    assert dec["fields"]["question"] == "存储用 JSON 还是 SQLite"
    assert dec["fields"]["choice"] == "JSON 优先"


def test_dock_list_title_only_decision_falls_back(monkeypatch, tmp_path, capsys):
    """An extraction-written decision keeps its primary text in `title`
    (question is null) — dock-list must surface it, never render '(decision)'."""
    store = tmp_path / "store"
    eng = _populate(store)
    eng.add_decision({"title": "采用方案X作为长期架构", "choice": ""})
    assert _cli_list(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    dec = next(r for r in payload["results"] if r["kind"] == "decision")
    assert dec["title"] == "采用方案X作为长期架构"
    assert dec["fields"]["question"] == "采用方案X作为长期架构"


def test_dock_list_excludes_archived(monkeypatch, tmp_path, capsys):
    from piia_engram.setup_wizard import _run_dock_archive

    store = tmp_path / "store"
    eng = _populate(store)
    lid = eng.add_lesson({"summary": "to be archived"}).get("id")
    eng.add_lesson({"summary": "stays active"})
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    assert _run_dock_archive(["--id", lid, "--json"]) == 0
    capsys.readouterr()  # drain the archive output
    assert _cli_list(monkeypatch, tmp_path, ["--json"]) == 0
    titles = [r["title"] for r in json.loads(capsys.readouterr().out)["results"]]
    assert "stays active" in titles
    assert "to be archived" not in titles


def test_dock_list_is_zero_write(monkeypatch, tmp_path):
    store = tmp_path / "store"
    eng = _populate(store)
    eng.add_lesson({"summary": "snapshot me"})
    before = _snapshot(store)
    _cli_list(monkeypatch, tmp_path, ["--json"])
    assert _snapshot(store) == before  # the whole list path wrote nothing


def test_dock_list_rejects_bad_limit(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_list(monkeypatch, tmp_path, ["--limit", "0", "--json"]) == 2
    assert _cli_list(monkeypatch, tmp_path, ["--limit", "x", "--json"]) == 2


def test_dock_list_limit_caps_results(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    eng = _populate(store)
    for n in range(5):
        eng.add_lesson({"summary": f"lesson {n}"})
    assert _cli_list(monkeypatch, tmp_path, ["--limit", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 2


def test_dock_list_json_arg_error_stays_json(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli_list(monkeypatch, tmp_path, ["--json", "--bogus"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


# --- dock-set-lang / dock-get-lang: owner language toggle (中英切换) ----------


def _cli_set_lang(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_set_lang

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_set_lang(argv)


def _cli_get_lang(monkeypatch, tmp_path, argv):
    from piia_engram.setup_wizard import _run_dock_get_lang

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return _run_dock_get_lang(argv)


def test_dock_set_lang_help(monkeypatch, tmp_path, capsys):
    assert _cli_set_lang(monkeypatch, tmp_path, ["--help"]) == 0
    assert "dock-set-lang" in capsys.readouterr().out


def test_dock_set_lang_requires_lang(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_set_lang(monkeypatch, tmp_path, ["--json"]) == 2


def test_dock_set_lang_rejects_bad_value(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli_set_lang(monkeypatch, tmp_path, ["--lang", "fr", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_dock_set_lang_value_cannot_be_a_flag(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_set_lang(monkeypatch, tmp_path, ["--lang", "--json"]) == 2


def test_dock_set_lang_writes_profile_language(monkeypatch, tmp_path, capsys):
    store = tmp_path / "store"
    _populate(store)
    assert _cli_set_lang(monkeypatch, tmp_path, ["--lang", "en", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["lang"] == "en"
    assert payload["language"] == "English"
    profile = json.loads(
        (store / "identity" / "profile.json").read_text(encoding="utf-8")
    )
    assert profile.get("language") == "English"


def test_dock_set_lang_zh_stores_chinese_label(monkeypatch, tmp_path):
    store = tmp_path / "store"
    _populate(store)
    assert _cli_set_lang(monkeypatch, tmp_path, ["--lang", "zh"]) == 0
    profile = json.loads(
        (store / "identity" / "profile.json").read_text(encoding="utf-8")
    )
    assert profile.get("language") == "中文"


def test_dock_get_lang_default_is_zh(monkeypatch, tmp_path, capsys):
    _populate(tmp_path / "store")
    assert _cli_get_lang(monkeypatch, tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["lang"] == "zh"


def test_dock_get_lang_is_zero_write(monkeypatch, tmp_path):
    store = tmp_path / "store"
    _populate(store)
    before = _snapshot(store)
    _cli_get_lang(monkeypatch, tmp_path, ["--json"])
    assert _snapshot(store) == before


def test_dock_get_lang_unknown_option(monkeypatch, tmp_path):
    _populate(tmp_path / "store")
    assert _cli_get_lang(monkeypatch, tmp_path, ["--bogus", "--json"]) == 2


def test_dock_set_then_get_lang_round_trip(monkeypatch, tmp_path, capsys):
    """The toggle persists so the dock reads its own language on next launch."""
    _populate(tmp_path / "store")
    assert _cli_set_lang(monkeypatch, tmp_path, ["--lang", "en"]) == 0
    capsys.readouterr()
    assert _cli_get_lang(monkeypatch, tmp_path, ["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lang"] == "en"
