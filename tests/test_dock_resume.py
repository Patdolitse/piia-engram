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


# --- Codex review-1 fixes: crypto / audit / real entry / arg edges ----------


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


# --- Codex review-2 fixes: flag-like values + reminder sentinel -------------


def test_dock_resume_project_value_cannot_be_a_flag(monkeypatch, tmp_path):
    # `--project --json` must NOT swallow the flag as the project value.
    assert _cli(monkeypatch, tmp_path, ["--project", "--json"]) == 2
    assert _cli(monkeypatch, tmp_path, ["--project", "--bogus"]) == 2


def test_dock_resume_budget_value_cannot_be_a_flag(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, ["--budget", "--json"]) == 2


# --- dock-search CLI + read_only migration zero-write (Codex D2 review) ------


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
    under read_only — never rewritten to disk (Codex D2 review must-fix)."""
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
    # The --json contract must hold even on arg-parse errors (Codex D3 must-fix).
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
    # The --json contract holds even on arg errors (Codex D3 v2 suggestion).
    assert _cli_portrait(monkeypatch, tmp_path, ["--json", "--bogus"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["markdown"] == ""


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
