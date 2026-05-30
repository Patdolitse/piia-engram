"""CLI review / approval workflow tests."""

from __future__ import annotations

import json
from pathlib import Path


def _read_lessons(root: Path) -> list[dict]:
    return json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))


def _read_decisions(root: Path) -> list[dict]:
    return json.loads((root / "knowledge" / "decisions.json").read_text(encoding="utf-8"))


def test_review_empty_state_is_successful(tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    assert run_review([]) == 0

    out = capsys.readouterr().out
    assert "No staging knowledge" in out


def test_review_lists_staging_metadata_without_touching_access(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    staging = eng.add_lesson(
        "staging lesson summary",
        detail="PRIVATE DETAIL BODY",
        tier="staging",
        access_count=0,
    )
    verified = eng.add_lesson("verified lesson summary")
    path = tmp_path / "knowledge" / "lessons.json"
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    assert run_review([]) == 0

    out = capsys.readouterr().out
    assert staging["id"] in out
    assert "staging lesson summary" in out
    assert verified["id"] not in out
    assert "verified lesson summary" not in out
    assert "PRIVATE DETAIL BODY" not in out
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    stored = next(item for item in _read_lessons(tmp_path) if item["id"] == staging["id"])
    assert stored.get("access_count", 0) == 0


def test_review_show_prints_one_item_body(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson(
        "show me",
        detail="Detailed review body",
        domain="review",
        tier="staging",
    )

    assert run_review(["show", lesson["id"]]) == 0

    out = capsys.readouterr().out
    assert lesson["id"] in out
    assert "show me" in out
    assert "Detailed review body" in out


def test_review_show_missing_returns_nonzero(tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    assert run_review(["show", "missing-id"]) == 1

    assert "not found" in capsys.readouterr().out.lower()


def test_review_approve_requires_yes_and_does_not_write_without_it(
    tmp_path, monkeypatch, capsys
):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson("needs approval", tier="staging")
    path = tmp_path / "knowledge" / "lessons.json"
    before = path.read_bytes()

    assert run_review(["approve", lesson["id"]]) == 2

    out = capsys.readouterr().out
    assert "--yes" in out
    assert path.read_bytes() == before


def test_review_approve_promotes_staging_with_yes(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson("approve me", tier="staging")

    assert run_review(["approve", lesson["id"], "--yes"]) == 0

    out = capsys.readouterr().out
    assert "promoted" in out.lower()
    stored = next(item for item in _read_lessons(tmp_path) if item["id"] == lesson["id"])
    assert stored["tier"] == "verified"


def test_review_approve_rejects_non_staging_item(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson("already verified")

    assert run_review(["approve", lesson["id"], "--yes"]) == 1

    out = capsys.readouterr().out
    assert "not staging" in out.lower()
    stored = next(item for item in _read_lessons(tmp_path) if item["id"] == lesson["id"])
    assert stored["tier"] == "verified"


def test_review_archive_requires_yes_and_does_not_write_without_it(
    tmp_path, monkeypatch, capsys
):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson("archive candidate", tier="staging")
    path = tmp_path / "knowledge" / "lessons.json"
    before = path.read_bytes()

    assert run_review(["archive", lesson["id"]]) == 2

    out = capsys.readouterr().out
    assert "--yes" in out
    assert path.read_bytes() == before


def test_review_archive_marks_item_outdated_with_yes(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    decision = Engram().add_decision(
        "archive this decision",
        choice="yes",
        reasoning="staging duplicate",
        tier="staging",
    )

    assert run_review(["archive", decision["id"], "--yes"]) == 0

    out = capsys.readouterr().out
    assert "archived" in out.lower()
    stored = next(item for item in _read_decisions(tmp_path) if item["id"] == decision["id"])
    assert stored["status"] == "outdated"


def test_review_main_dispatches(tmp_path, monkeypatch):
    import piia_engram.setup_wizard as sw

    seen = {}

    def fake_run_review(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(sw, "run_review", fake_run_review)
    monkeypatch.setattr("sys.argv", ["engram", "review", "--limit", "5"])

    try:
        sw.main()
    except SystemExit as exc:
        assert exc.code == 0

    assert seen["argv"] == ["--limit", "5"]
