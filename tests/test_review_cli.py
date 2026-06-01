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


def test_review_list_surfaces_quality_without_body_leak(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    Engram().add_lesson({
        "summary": "quality scored lesson",
        "detail": "PRIVATE DETAIL BODY",
        "tier": "staging",
        "extraction": {
            "method": "notes",
            "quality_score": 0.82,
            "quality_signals": ["evidence_or_outcome"],
            "quality_flags": [],
            "evidence_span": "PRIVATE EVIDENCE BODY",
        },
    })

    assert run_review([]) == 0

    out = capsys.readouterr().out
    assert "q=0.82" in out
    assert "notes" in out
    assert "quality scored lesson" in out
    assert "PRIVATE DETAIL BODY" not in out
    assert "PRIVATE EVIDENCE BODY" not in out


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


def test_review_show_prints_quality_metadata(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson({
        "summary": "show quality",
        "tier": "staging",
        "extraction": {
            "method": "session_insights",
            "source_tool": "codex",
            "quality_score": 0.91,
            "quality_signals": ["durable_rule", "concrete_context"],
            "quality_flags": ["reviewable"],
            "evidence_span": "Remember to run twine check before publishing",
        },
    })

    assert run_review(["show", lesson["id"]]) == 0

    out = capsys.readouterr().out
    assert "quality: q=0.91" in out
    assert "session_insights" in out
    assert "durable_rule" in out
    assert "Remember to run twine check" in out


def test_review_show_truncates_long_evidence(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    long_evidence = "A" * 320
    lesson = Engram().add_lesson({
        "summary": "show bounded evidence",
        "tier": "staging",
        "extraction": {
            "method": "session_insights",
            "quality_score": 0.72,
            "quality_signals": ["durable_rule"],
            "evidence_span": long_evidence,
        },
    })

    assert run_review(["show", lesson["id"]]) == 0

    out = capsys.readouterr().out
    assert "A" * 240 not in out
    assert "..." in out


def test_review_quality_metadata_strips_terminal_control_chars(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson({
        "summary": "control char quality",
        "tier": "staging",
        "extraction": {
            "method": "\x1b[31mnotes",
            "source_tool": "codex\x1b[0m",
            "quality_score": 0.73,
            "quality_signals": ["durable_rule\x1b[31m"],
            "evidence_span": "safe evidence\x1b[0m",
        },
    })

    assert run_review([]) == 0
    list_out = capsys.readouterr().out
    assert "\x1b" not in list_out
    assert "[31mnotes" in list_out

    assert run_review(["show", lesson["id"]]) == 0
    show_out = capsys.readouterr().out
    assert "\x1b" not in show_out
    assert "durable_rule" in show_out


def test_review_list_can_sort_by_quality(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    low = eng.add_lesson({
        "summary": "low confidence candidate",
        "tier": "staging",
        "extraction": {"method": "notes", "quality_score": 0.41},
    })
    high = eng.add_lesson({
        "summary": "high confidence candidate",
        "tier": "staging",
        "extraction": {"method": "notes", "quality_score": 0.93},
    })

    assert run_review(["--sort", "quality"]) == 0

    out = capsys.readouterr().out
    assert out.index(low["id"]) < out.index(high["id"])


def test_review_list_quality_sort_is_stable_for_equal_scores(
    tmp_path, monkeypatch, capsys
):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    first = eng.add_lesson({
        "summary": "first equal quality candidate",
        "tier": "staging",
        "timestamp": "2026-06-01T00:00:00Z",
        "extraction": {"method": "notes", "quality_score": 0.75},
    })
    second = eng.add_lesson({
        "summary": "second equal quality candidate",
        "tier": "staging",
        "timestamp": "2026-06-01T00:00:00Z",
        "extraction": {"method": "notes", "quality_score": 0.75},
    })

    assert run_review(["--sort", "quality"]) == 0

    out = capsys.readouterr().out
    assert out.index(first["id"]) < out.index(second["id"])


def test_review_list_low_quality_filter_includes_missing_scores(
    tmp_path, monkeypatch, capsys
):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    low = eng.add_lesson({
        "summary": "low confidence candidate",
        "tier": "staging",
        "extraction": {"method": "notes", "quality_score": 0.41},
    })
    missing = eng.add_lesson("legacy staging candidate without score", tier="staging")
    high = eng.add_lesson({
        "summary": "high confidence candidate",
        "tier": "staging",
        "extraction": {"method": "notes", "quality_score": 0.93},
    })

    assert run_review(["--low-quality"]) == 0

    out = capsys.readouterr().out
    assert low["id"] in out
    assert missing["id"] in out
    assert high["id"] not in out


def test_review_list_rejects_invalid_sort(tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    assert run_review(["--sort", "random"]) == 2

    out = capsys.readouterr().out
    assert "Invalid review sort" in out


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
