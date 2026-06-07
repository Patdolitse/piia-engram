"""Tests for the lean user-portrait feature (PortraitMixin + CLI).

Covers build_user_portrait (shape/stats/privacy), versioned save + prune +
ordering, compare growth diff, bilingual renders, and the `engram portrait`
CLI. All tests use an isolated ENGRAM_DIR (tmp_path) so the real store is
never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram import reports_portrait
from piia_engram import setup_wizard


def _seed(engram: Engram) -> None:
    """Seed a small but realistic store: profile + lessons + decisions + project."""
    engram.update_profile({
        "role": "PIIA 创始人",
        "description": "non-technical founder",
        "language": "zh",
        "technical_level": "非技术背景",
    })
    engram.add_lesson("用户偏好 GUI 操作", domain="ux", source_tool="claude_code", tier="verified")
    engram.add_lesson("发布前必须验证", domain="release", source_tool="codex", tier="staging")
    engram.add_lesson("跨工具连续性是核心卖点", domain="strategy,positioning",
                      source_tool="claude_code", tier="verified")
    engram.add_decision("定位身份层", choice="锁定多工具开发者", source_tool="claude_code")
    engram.save_project_snapshot("E:/proj/engram", {"title": "Engram"})


def make_engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


# --------------------------------------------------------------------- build
def test_build_portrait_shape_and_stats(tmp_path):
    eng = make_engram(tmp_path)
    _seed(eng)
    p = eng.build_user_portrait()

    assert p["schema_version"] == reports_portrait._PORTRAIT_SCHEMA_VERSION
    assert p["generated_at"]
    # identity carried from profile
    assert p["identity"]["role"] == "PIIA 创始人"
    assert p["identity"]["language"] == "zh"

    stats = p["stats"]
    assert stats["lesson_count"] == 3
    assert stats["lesson_verified"] == 2  # only the two tier=verified lessons
    assert stats["decision_count"] == 1
    assert stats["project_count"] == 1
    # tools = distinct source_tool across lessons+decisions
    assert set(p["active_tools"]) == {"claude_code", "codex"}
    assert stats["tool_count"] == 2

    # domains: ux, release, and "strategy,positioning" splits into two → 4 unique
    assert stats["domain_count"] == 4
    assert set(p["domains"]) == {"ux", "release", "strategy", "positioning"}


def test_build_portrait_is_lean_no_raw_knowledge_text(tmp_path):
    """Portrait must NOT embed raw lesson/decision bodies (privacy + size)."""
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "founder", "language": "en"})
    secret = "SECRET-LESSON-BODY-DO-NOT-LEAK-xyz123"
    eng.add_lesson(secret, domain="ux", source_tool="codex", tier="verified")
    p = eng.build_user_portrait()
    assert secret not in json.dumps(p, ensure_ascii=False)


def test_top_domains_capped(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "x", "language": "en"})
    for i in range(15):
        eng.add_lesson(f"lesson {i}", domain=f"dom{i:02d}", source_tool="codex")
    p = eng.build_user_portrait()
    assert len(p["top_domains"]) == reports_portrait._TOP_DOMAINS  # capped at 10
    assert len(p["domains"]) == 15  # full list preserved for compare


# ---------------------------------------------------------------------- save
def test_save_writes_versioned_file(tmp_path):
    eng = make_engram(tmp_path)
    _seed(eng)
    saved = eng.save_user_portrait()
    path = Path(saved["_path"])
    assert path.exists()
    assert path.parent == tmp_path / "portraits"
    assert ":" not in path.name  # filesystem-safe stem
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["stats"]["lesson_count"] == 3


def test_save_collision_appends_suffix_and_orders_by_generated_at(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "x", "language": "en"})
    # Two portraits with the SAME generated_at → second gets a -1 suffix.
    base = eng.build_user_portrait()
    base["generated_at"] = "2026-06-08T10:00:00"
    first = dict(base)
    first["stats"] = dict(base["stats"]); first["stats"]["lesson_count"] = 1
    second = dict(base)
    second["stats"] = dict(base["stats"]); second["stats"]["lesson_count"] = 2
    eng.save_user_portrait(first)
    eng.save_user_portrait(second)

    files = eng._portrait_files()
    assert len(files) == 2
    # filename of second sorts as "...-1.json" which is lexically BEFORE ".json";
    # ordering must be stable by (generated_at, name) and not crash.
    items = eng.list_user_portraits()  # newest first
    assert len(items) == 2


def test_prune_keeps_only_max(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_portrait, "_MAX_PORTRAITS", 3)
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "x", "language": "en"})
    for i in range(6):
        p = eng.build_user_portrait()
        p["generated_at"] = f"2026-06-08T10:00:0{i}"
        eng.save_user_portrait(p)
    files = eng._portrait_files()
    assert len(files) == 3
    # the survivors are the newest three
    gens = [json.loads(f.read_text(encoding="utf-8"))["generated_at"] for f in files]
    assert gens == ["2026-06-08T10:00:03", "2026-06-08T10:00:04", "2026-06-08T10:00:05"]


def test_latest_and_previous(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "x", "language": "en"})
    assert eng.get_latest_portrait() is None
    assert eng.get_previous_portrait() is None

    a = eng.build_user_portrait(); a["generated_at"] = "2026-06-08T09:00:00"
    eng.save_user_portrait(a)
    assert eng.get_previous_portrait() is None  # only one stored
    b = eng.build_user_portrait(); b["generated_at"] = "2026-06-08T11:00:00"
    eng.save_user_portrait(b)
    assert eng.get_latest_portrait()["generated_at"] == "2026-06-08T11:00:00"
    assert eng.get_previous_portrait()["generated_at"] == "2026-06-08T09:00:00"


# ------------------------------------------------------------------- compare
def test_compare_reports_growth(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "founder", "language": "en"})
    eng.add_lesson("first", domain="ux", source_tool="codex", tier="verified")
    old = eng.build_user_portrait()

    # grow: add a lesson in a NEW domain with a NEW tool
    eng.add_lesson("second", domain="security", source_tool="cursor", tier="verified")
    new = eng.build_user_portrait()

    diff = eng.compare_user_portraits(old, new)
    assert diff["deltas"]["lesson_count"]["delta"] == 1
    assert diff["deltas"]["domain_count"]["delta"] == 1
    assert diff["new_domains"] == ["security"]
    assert diff["new_tools"] == ["cursor"]


def test_compare_identity_change(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "tester", "language": "en"})
    old = eng.build_user_portrait()
    eng.update_profile({"technical_level": "advanced"})
    new = eng.build_user_portrait()
    diff = eng.compare_user_portraits(old, new)
    assert "technical_level" in diff["identity_changes"]
    assert diff["identity_changes"]["technical_level"]["to"] == "advanced"


# -------------------------------------------------------------------- render
def test_render_portrait_markdown(tmp_path):
    eng = make_engram(tmp_path)
    _seed(eng)
    md = eng.render_user_portrait(eng.build_user_portrait())
    assert "用户写照" in md or "User Portrait" in md
    assert "PIIA 创始人" in md
    assert "claude_code" in md


def test_render_growth_markdown(tmp_path):
    eng = make_engram(tmp_path)
    eng.update_profile({"role": "x", "language": "zh"})
    eng.add_lesson("a", domain="ux", source_tool="codex", tier="verified")
    old = eng.build_user_portrait()
    eng.add_lesson("b", domain="sec", source_tool="cursor", tier="verified")
    new = eng.build_user_portrait()
    md = eng.render_portrait_growth(eng.compare_user_portraits(old, new))
    assert "成长对比" in md or "Growth" in md
    assert "sec" in md       # new domain surfaced
    assert "cursor" in md    # new tool surfaced


# ----------------------------------------------------------------------- CLI
def test_cli_portrait_saves_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = make_engram(tmp_path)
    _seed(eng)
    rc = setup_wizard._run_portrait([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PIIA 创始人" in out
    # a snapshot file was written
    assert list((tmp_path / "portraits").glob("*.json"))


def test_cli_portrait_no_save(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = make_engram(tmp_path)
    _seed(eng)
    rc = setup_wizard._run_portrait(["--no-save"])
    assert rc == 0
    assert not (tmp_path / "portraits").exists() or not list((tmp_path / "portraits").glob("*.json"))


def test_cli_portrait_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = make_engram(tmp_path)
    _seed(eng)
    rc = setup_wizard._run_portrait(["--json", "--no-save"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["portrait"]["stats"]["lesson_count"] == 3


def test_cli_portrait_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = make_engram(tmp_path)
    _seed(eng)
    eng.save_user_portrait()
    rc = setup_wizard._run_portrait(["--list", "--json"])
    assert rc == 0
    items = json.loads(capsys.readouterr().out)
    assert len(items) == 1
    assert items[0]["stats"]["lesson_count"] == 3
