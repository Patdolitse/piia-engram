"""Presence loop — Build 3 (Layer 3): `engram weekly` recap + once-per-week hint.

Design locked with Codex (2026-06-19):
  - The `engram weekly` COMMAND is strictly READ-ONLY (no state write, no access
    bump). The ONLY write is the SessionStart hint dedup state, confined to the
    hook layer (never get_resume_brief) — preserving read-path purity (the same
    write-boundary class we fixed in Build 1).
  - Recap ≤10 lines, missing sections omitted (no noise).
  - Resurface pick is DETERMINISTIC (no Readwise-style random noise):
    project-relevant first, then oldest last_reviewed (fallback created_at),
    then oldest created_at, then id tie-break; omit when zero candidates.

Wave 1: the pure functions render_weekly_text + select_resurface.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Import setup_wizard first so the pre-existing setup_wizard<->cli_commands
# import cycle resolves in the real CLI load order before we import handlers.
import piia_engram.setup_wizard  # noqa: F401
from piia_engram.core import Engram
from piia_engram.reports_weekly import (
    build_weekly_recap,
    render_weekly_text,
    select_resurface,
)


# ---------------------------------------------------------------------------
# render_weekly_text — pure formatter
# ---------------------------------------------------------------------------


def _full_recap() -> dict:
    return {
        "start": "2026-06-12",
        "end": "2026-06-19",
        "counts": {"lessons": 5, "decisions": 2, "playbooks": 1, "needs_review": 3},
        "top_domains": [
            {"domain": "python", "count": 4},
            {"domain": "testing", "count": 2},
            {"domain": "mcp", "count": 1},
        ],
        "growth": [
            {"stat": "lessons_total", "from": 10, "to": 15, "delta": 5},
            {"stat": "domains", "from": 3, "to": 4, "delta": 1},
        ],
        "daily_log_titles": ["shipped recall header", "fixed write boundary"],
        "resurface": {"kind": "lesson", "id": "x1", "summary": "pin CI deps", "domain": "python"},
    }


class TestRenderWeeklyText:
    def test_header_line_format(self):
        text = render_weekly_text(_full_recap())
        first = text.splitlines()[0]
        assert first == (
            "[Engram] Week of 2026-06-12-2026-06-19: "
            "+5 lessons, +2 decisions, +1 playbook · 3 need review"
        )

    def test_full_recap_lines(self):
        lines = render_weekly_text(_full_recap()).splitlines()
        assert any(l.startswith("Top domains: python 4, testing 2, mcp 1") for l in lines)
        assert any(l.startswith("Growth: lessons_total +5, domains +1") for l in lines)
        assert any(l.startswith("Daily log: shipped recall header; fixed write boundary") for l in lines)
        assert any(l == "Resurface: pin CI deps" for l in lines)

    def test_omits_empty_sections(self):
        recap = {
            "start": "2026-06-12", "end": "2026-06-19",
            "counts": {"lessons": 0, "decisions": 0, "playbooks": 0, "needs_review": 0},
            "top_domains": [], "growth": [], "daily_log_titles": [], "resurface": None,
        }
        lines = render_weekly_text(recap).splitlines()
        assert len(lines) == 1  # only the header
        assert lines[0].startswith("[Engram] Week of")

    def test_never_exceeds_10_lines(self):
        recap = _full_recap()
        recap["top_domains"] = [{"domain": f"d{i}", "count": i} for i in range(20)]
        recap["daily_log_titles"] = [f"t{i}" for i in range(20)]
        recap["growth"] = [{"stat": f"s{i}", "from": 0, "to": i, "delta": i} for i in range(20)]
        assert len(render_weekly_text(recap).splitlines()) <= 10


# ---------------------------------------------------------------------------
# select_resurface — deterministic, non-random
# ---------------------------------------------------------------------------


def _lesson(id, domain, *, tier="verified", last_reviewed="", created_at=""):
    return {"id": id, "summary": f"sum-{id}", "domain": domain, "tier": tier,
            "last_reviewed": last_reviewed, "created_at": created_at, "status": "active"}


class TestSelectResurface:
    def test_project_relevant_oldest_wins(self):
        lessons = [
            _lesson("a", "python", last_reviewed="2026-01-01T00:00:00"),  # relevant, older
            _lesson("b", "rust", last_reviewed="2025-01-01T00:00:00"),    # irrelevant, oldest
            _lesson("c", "python", last_reviewed="2026-06-01T00:00:00"),  # relevant, newer
        ]
        picked = select_resurface(lessons, project_tokens={"python"})
        assert picked is not None and picked["id"] == "a"

    def test_no_project_picks_global_oldest(self):
        lessons = [
            _lesson("a", "python", last_reviewed="2026-01-01T00:00:00"),
            _lesson("b", "rust", last_reviewed="2025-01-01T00:00:00"),
        ]
        picked = select_resurface(lessons, project_tokens=None)
        assert picked is not None and picked["id"] == "b"

    def test_falls_back_to_created_at_when_no_last_reviewed(self):
        lessons = [
            _lesson("a", "go", created_at="2026-05-01T00:00:00"),
            _lesson("b", "go", created_at="2025-05-01T00:00:00"),
        ]
        picked = select_resurface(lessons, project_tokens=None)
        assert picked is not None and picked["id"] == "b"

    def test_skips_non_verified(self):
        lessons = [_lesson("a", "python", tier="staging", last_reviewed="2020-01-01T00:00:00")]
        assert select_resurface(lessons, project_tokens={"python"}) is None

    def test_empty_returns_none(self):
        assert select_resurface([], project_tokens={"python"}) is None

    def test_tie_break_by_id(self):
        lessons = [
            _lesson("z", "python", last_reviewed="2026-01-01T00:00:00"),
            _lesson("a", "python", last_reviewed="2026-01-01T00:00:00"),
        ]
        picked = select_resurface(lessons, project_tokens=None)
        assert picked is not None and picked["id"] == "a"


# ---------------------------------------------------------------------------
# build_weekly_recap — read-only gather over a real Engram
# ---------------------------------------------------------------------------


class TestBuildWeeklyRecap:
    def test_counts_recent_items(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        e.add_lesson("recent lesson 1", domain="python")
        e.add_lesson("recent lesson 2", domain="python,testing")
        e.add_decision("Database choice", choice="SQLite")
        e.add_playbook({"title": "Deploy", "triggers": ["deploy"]})

        recap = build_weekly_recap(e, project_folder=str(tmp_path))

        assert recap["counts"]["lessons"] == 2
        assert recap["counts"]["decisions"] == 1
        assert recap["counts"]["playbooks"] == 1
        assert recap["counts"]["needs_review"] == 0
        # top domains from recent lessons/decisions
        doms = {d["domain"]: d["count"] for d in recap["top_domains"]}
        assert doms.get("python") == 2 and doms.get("testing") == 1

    def test_excludes_items_older_than_7_days(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        e.add_lesson("old lesson", domain="python")
        # Evaluate the window from far in the future so the item is >7 days old.
        recap = build_weekly_recap(e, now=datetime(2030, 1, 1), project_folder=str(tmp_path))
        assert recap["counts"]["lessons"] == 0

    def test_resurface_is_a_lesson_or_none(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        e.add_lesson("resurfaceable", domain="python")
        recap = build_weekly_recap(e, project_folder=str(tmp_path))
        assert recap["resurface"] is not None
        assert recap["resurface"]["kind"] == "lesson"
        assert recap["resurface"]["summary"] == "resurfaceable"

    def test_recent_daily_log_titles(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        proj = str(tmp_path / "proj")
        Path(proj).mkdir()
        e.append_daily_log(proj, "shipped the recall header", event_type="session")
        recap = build_weekly_recap(e, project_folder=proj)
        assert any("shipped the recall header" in t for t in recap["daily_log_titles"])

    def test_recap_text_is_at_most_10_lines(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        for i in range(5):
            e.add_lesson(f"lesson {i}", domain=f"d{i}")
        recap = build_weekly_recap(e, project_folder=str(tmp_path))
        assert len(render_weekly_text(recap).splitlines()) <= 10


# ---------------------------------------------------------------------------
# CLI wiring — `engram weekly` / `engram weekly --json`
# ---------------------------------------------------------------------------


class TestWeeklyCli:
    def test_run_weekly_text(self, tmp_path: Path, monkeypatch, capsys):
        from piia_engram.cli_commands import _run_weekly

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        e = Engram(root=tmp_path)
        e.add_lesson("cli lesson", domain="python")

        rc = _run_weekly([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "[Engram] Week of" in out

    def test_run_weekly_json(self, tmp_path: Path, monkeypatch, capsys):
        import json as _json
        from piia_engram.cli_commands import _run_weekly

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        e = Engram(root=tmp_path)
        e.add_lesson("cli lesson", domain="python")

        rc = _run_weekly(["--json"])
        out = capsys.readouterr().out
        assert rc == 0
        parsed = _json.loads(out)
        assert "counts" in parsed and parsed["counts"]["lessons"] == 1

    def test_weekly_dispatched_by_main(self, tmp_path: Path, monkeypatch, capsys):
        import piia_engram.setup_wizard as W

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        Engram(root=tmp_path).add_lesson("dispatch lesson", domain="python")
        monkeypatch.setattr(sys, "argv", ["engram", "weekly"])

        with pytest.raises(SystemExit) as ei:
            W.main()
        assert ei.value.code == 0
        assert "[Engram] Week of" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Weekly hint — the ONLY Layer-3 write, confined to the hook layer
# ---------------------------------------------------------------------------


class TestWeeklyHint:
    def test_appends_hint_when_due_with_activity(self, tmp_path: Path):
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        e = Engram(root=tmp_path)
        e.add_lesson("hint lesson", domain="python")
        state = tmp_path / "weekly_hint_state.json"

        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), engram=e, state_path=state
        )
        assert out.startswith("BRIEF")
        assert "[Engram Weekly] +1 this week, 0 need review — run 'engram weekly'" in out
        assert state.exists()  # dedup state written when shown

    def test_suppressed_within_7_day_window(self, tmp_path: Path):
        import json
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        e = Engram(root=tmp_path)
        e.add_lesson("x", domain="python")
        state = tmp_path / "weekly_hint_state.json"
        state.write_text(json.dumps({"last_shown": datetime.now().isoformat()}), encoding="utf-8")

        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), engram=e, state_path=state
        )
        assert out == "BRIEF"
        assert "[Engram Weekly]" not in out

    def test_due_again_after_7_days(self, tmp_path: Path):
        import json
        from datetime import timedelta
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        e = Engram(root=tmp_path)
        e.add_lesson("x", domain="python")
        state = tmp_path / "weekly_hint_state.json"
        state.write_text(
            json.dumps({"last_shown": (datetime.now() - timedelta(days=8)).isoformat()}),
            encoding="utf-8",
        )

        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), engram=e, state_path=state
        )
        assert "[Engram Weekly]" in out

    def test_no_activity_no_hint_and_no_write(self, tmp_path: Path):
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        e = Engram(root=tmp_path)  # empty — nothing this week, nothing to review
        state = tmp_path / "weekly_hint_state.json"

        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), engram=e, state_path=state
        )
        assert out == "BRIEF"
        assert not state.exists()  # no slot consumed when nothing to nudge

    def test_fail_silent_never_breaks_session(self, tmp_path: Path):
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        class Boom:
            def get_lessons(self, **k):
                raise RuntimeError("boom")

        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), engram=Boom(), state_path=tmp_path / "s.json"
        )
        assert out == "BRIEF"


# ---------------------------------------------------------------------------
# Zero-write guarantee (Codex final review): the recap read path must not write
# audit.log / create the store, even with ENGRAM_AUDIT forced ON.
# ---------------------------------------------------------------------------


def _snap(root: Path) -> dict:
    import hashlib

    out: dict[str, str] = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class TestWeeklyZeroWrite:
    def test_engram_weekly_writes_nothing_with_audit_on(self, tmp_path: Path, monkeypatch):
        from piia_engram.cli_commands import _run_weekly

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        Engram(root=tmp_path).add_lesson("zw lesson", domain="python")
        # Force audit ON: a plain Engram() would append audit.log on every read;
        # _run_weekly must use Engram(read_only=True) and write nothing.
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        before = _snap(tmp_path)
        rc = _run_weekly([])
        after = _snap(tmp_path)
        assert rc == 0
        assert after == before, (
            f"engram weekly mutated the store: {sorted(set(before) ^ set(after))}"
        )

    def test_weekly_hint_no_nudge_writes_nothing(self, tmp_path: Path, monkeypatch):
        from piia_engram.hooks._weekly_hint import maybe_append_weekly_hint

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        # Empty store → no new memories, nothing to review → no nudge, no slot
        # consumed, and the read_only recap must not write audit.log either.
        before = _snap(tmp_path)
        out = maybe_append_weekly_hint(
            "BRIEF", project_folder=str(tmp_path), state_path=tmp_path / "wh.json"
        )
        after = _snap(tmp_path)
        assert out == "BRIEF"
        assert after == before, (
            f"weekly hint (no-nudge) mutated the store: {sorted(set(before) ^ set(after))}"
        )

    def test_build_weekly_recap_zero_write_even_with_writable_engram(
        self, tmp_path: Path, monkeypatch
    ):
        # The recap's read-only contract must hold at the HELPER level, not just
        # via its two production callers. build_weekly_recap has no enforcement
        # of its own, so a future entry point that hands it a plain Engram()
        # (instead of Engram(read_only=True)) would silently re-expose the audit
        # read-write — every get_* logs a "read" to audit.log. Calling the helper
        # directly with a writable, audit-ON engram must STILL write nothing, and
        # its reads must still return the seeded data.
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        Engram(root=tmp_path).add_lesson("zw lesson", domain="python")
        monkeypatch.setenv("ENGRAM_AUDIT", "1")
        writable = Engram(root=tmp_path)  # read_only=False → audit enabled
        assert writable._read_only is False  # precondition: a real write surface

        before = _snap(tmp_path)
        recap = build_weekly_recap(writable, project_folder=str(tmp_path))
        after = _snap(tmp_path)

        assert recap["counts"]["lessons"] == 1  # reads still work after the guard
        assert after == before, (
            "build_weekly_recap mutated the store via a writable engram: "
            f"{sorted(set(before) ^ set(after))}"
        )
