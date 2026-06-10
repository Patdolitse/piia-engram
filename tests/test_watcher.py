"""Tests for the universal watcher (core + codex adapter).

Covers src/piia_engram/watcher/{core.py, codex_adapter.py, __main__.py}.

Isolation rules: ENGRAM_DIR always points into tmp_path, the codex sessions
root is overridden into tmp_path, and the Engram core class is replaced by a
recording stub — no test ever touches a real store or real Codex data.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram.watcher import codex_adapter, core


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-state"))
    monkeypatch.setenv(codex_adapter.ENV_SESSIONS_ROOT, str(tmp_path / "codex-sessions"))
    monkeypatch.delenv("ENGRAM_WATCHER_DEBOUNCE", raising=False)
    monkeypatch.delenv("ENGRAM_WATCHER_SINCE_DAYS", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)


@pytest.fixture()
def fake_engram(monkeypatch):
    calls: list[dict] = []

    class FakeEngram:
        raise_on_save = False

        def save_agent_context(self, **kwargs):
            calls.append(kwargs)
            if FakeEngram.raise_on_save:
                raise RuntimeError("boom")
            return {"ok": True}

    monkeypatch.setattr("piia_engram.core.Engram", FakeEngram)
    return SimpleNamespace(cls=FakeEngram, calls=calls)


def _day_dir(root: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    d = root / f"{when.year:04d}" / f"{when.month:02d}" / f"{when.day:02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_rollout(
    directory: Path,
    *,
    uuid: str = "0000aaaa-bbbb-cccc-dddd-eeeeffff0001",
    cwd: str = "E:\\\\Some Project",
    turns: list[tuple[str, str]] | None = None,
    extra_lines: list[str] | None = None,
) -> Path:
    path = directory / f"rollout-2026-06-10T10-00-00-{uuid}.jsonl"
    lines = [
        json.dumps(
            {"timestamp": "t", "type": "session_meta", "payload": {"id": uuid, "cwd": cwd}}
        )
    ]
    for role, message in turns or []:
        ptype = "user_message" if role == "user" else "agent_message"
        lines.append(
            json.dumps(
                {"timestamp": "t", "type": "event_msg", "payload": {"type": ptype, "message": message}},
                ensure_ascii=False,
            )
        )
    lines.extend(extra_lines or [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# codex adapter
# ---------------------------------------------------------------------------


def test_sessions_root_priority(monkeypatch, tmp_path):
    monkeypatch.setenv(codex_adapter.ENV_SESSIONS_ROOT, str(tmp_path / "override"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    assert codex_adapter.sessions_root() == tmp_path / "override"
    monkeypatch.delenv(codex_adapter.ENV_SESSIONS_ROOT)
    assert codex_adapter.sessions_root() == tmp_path / "home" / "sessions"
    monkeypatch.delenv("CODEX_HOME")
    assert codex_adapter.sessions_root() == Path("~/.codex").expanduser() / "sessions"


def test_discover_finds_recent_rollouts_only(tmp_path):
    root = codex_adapter.sessions_root()
    today_dir = _day_dir(root)
    recent = _write_rollout(today_dir, turns=[("user", "hi")])
    # Non-rollout file is ignored.
    (today_dir / "notes.txt").write_text("x", encoding="utf-8")
    # A file 10 days old sits outside the 3-day window.
    old_dir = _day_dir(root, datetime.now() - timedelta(days=10))
    _write_rollout(old_dir, uuid="0000aaaa-bbbb-cccc-dddd-eeeeffff0099")
    found = list(codex_adapter.discover(since_days=3))
    assert found == [recent]


def test_discover_missing_root_yields_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv(codex_adapter.ENV_SESSIONS_ROOT, str(tmp_path / "nope"))
    assert list(codex_adapter.discover()) == []


def test_parse_extracts_clean_conversation_and_meta(tmp_path):
    day = _day_dir(codex_adapter.sessions_root())
    noisy_response_item = json.dumps(
        {
            "timestamp": "t",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "# AGENTS.md instructions noise"}],
            },
        }
    )
    path = _write_rollout(
        day,
        cwd="\\\\?\\E:\\My Project",
        turns=[("user", "今天星期几"), ("assistant", "星期三")],
        extra_lines=[noisy_response_item, "not-json", json.dumps({"type": "event_msg", "payload": {"type": "token_count"}})],
    )
    parsed = codex_adapter.parse(path)
    assert parsed["session_id"] == "0000aaaa-bbbb-cccc-dddd-eeeeffff0001"
    # Extended-length prefix stripped.
    assert parsed["project_folder"] == "E:\\My Project"
    assert "[user] 今天星期几" in parsed["summary"]
    assert "[assistant] 星期三" in parsed["summary"]
    # response_item pollution (AGENTS.md injection) must NOT leak in.
    assert "AGENTS.md" not in parsed["summary"]


def test_parse_session_id_falls_back_to_filename_stem(tmp_path):
    day = _day_dir(codex_adapter.sessions_root())
    path = day / "rollout-2026-06-10T10-00-00-deadbeef.jsonl"
    path.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}) + "\n",
        encoding="utf-8",
    )
    parsed = codex_adapter.parse(path)
    assert parsed["session_id"] == path.stem
    assert parsed["project_folder"] == ""


def test_parse_tail_truncation_keeps_conclusion(tmp_path):
    day = _day_dir(codex_adapter.sessions_root())
    turns = [("user", f"question {i} " + "x" * 100) for i in range(100)]
    turns.append(("assistant", "FINAL-ANSWER"))
    path = _write_rollout(day, turns=turns)
    parsed = codex_adapter.parse(path, max_chars=500)
    assert len(parsed["summary"]) <= 500
    assert "FINAL-ANSWER" in parsed["summary"]


# ---------------------------------------------------------------------------
# watcher core
# ---------------------------------------------------------------------------


def test_scan_saves_new_transcript_to_contexts(fake_engram):
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello"), ("assistant", "world")])
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["saved"] == 1
    assert len(fake_engram.calls) == 1
    call = fake_engram.calls[0]
    assert call["tool"] == "codex"
    assert call["session_id"] == "0000aaaa-bbbb-cccc-dddd-eeeeffff0001"
    assert "[user] hello" in call["content"]
    assert "[assistant] world" in call["content"]


def test_scan_skips_unchanged_files(fake_engram):
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello")])
    core.scan_once(["codex"], baseline_existing=False)
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["saved"] == 0
    assert counters["skipped"] >= 1
    assert len(fake_engram.calls) == 1


def test_first_run_baselines_without_saving(fake_engram):
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "old history")])
    counters = core.scan_once(["codex"])  # true first run -> baseline
    assert counters["baselined"] == 1
    assert counters["saved"] == 0
    assert fake_engram.calls == []


def test_changed_file_after_baseline_is_saved(fake_engram, monkeypatch):
    monkeypatch.setenv("ENGRAM_WATCHER_DEBOUNCE", "0")
    day = _day_dir(codex_adapter.sessions_root())
    path = _write_rollout(day, turns=[("user", "old history")])
    core.scan_once(["codex"])  # baseline
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "new reply"}})
            + "\n"
        )
    counters = core.scan_once(["codex"])
    assert counters["saved"] == 1
    assert "new reply" in fake_engram.calls[0]["content"]


def test_debounce_suppresses_rapid_resaves(fake_engram):
    day = _day_dir(codex_adapter.sessions_root())
    path = _write_rollout(day, turns=[("user", "turn one")])
    core.scan_once(["codex"], baseline_existing=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "turn two"}})
            + "\n"
        )
    counters = core.scan_once(["codex"], baseline_existing=False)  # default 10-min debounce
    assert counters["saved"] == 0
    assert len(fake_engram.calls) == 1


def test_save_failure_keeps_watermark_for_retry(fake_engram, monkeypatch):
    monkeypatch.setenv("ENGRAM_WATCHER_DEBOUNCE", "0")
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello")])
    fake_engram.cls.raise_on_save = True
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["errors"] == 1
    fake_engram.cls.raise_on_save = False
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["saved"] == 1  # retried because watermark did not advance


def test_meta_only_transcript_advances_watermark_without_save(fake_engram):
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[])  # session_meta only, no conversation
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["saved"] == 0
    assert fake_engram.calls == []
    counters = core.scan_once(["codex"], baseline_existing=False)
    assert counters["skipped"] >= 1


def test_state_prunes_files_outside_window(fake_engram, monkeypatch):
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello")])
    core.scan_once(["codex"], baseline_existing=False)
    state_file = Path(core._state_dir()) / "watcher_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["codex"]["E:/ghost/rollout-gone.jsonl"] = {"mtime": 1.0, "size": 1}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    core.scan_once(["codex"], baseline_existing=False)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "E:/ghost/rollout-gone.jsonl" not in state["codex"]


def test_unknown_adapter_is_error_not_crash(fake_engram):
    counters = core.scan_once(["definitely-not-a-tool"])
    assert counters["errors"] == 1
    assert fake_engram.calls == []


def test_never_touches_knowledge_store(fake_engram):
    """Contract: the watcher only calls save_agent_context, nothing else."""
    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello")])

    seen: list[str] = []

    class StrictEngram:
        def save_agent_context(self, **kwargs):
            seen.append("save_agent_context")
            return {"ok": True}

        def __getattr__(self, name):  # any other method call -> hard fail
            raise AssertionError(f"watcher must not call Engram.{name}")

    import piia_engram.core as core_mod

    original = core_mod.Engram
    core_mod.Engram = StrictEngram  # type: ignore[misc]
    try:
        core.scan_once(["codex"], baseline_existing=False)
    finally:
        core_mod.Engram = original  # type: ignore[misc]
    assert seen == ["save_agent_context"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_once_runs_and_prints_counters(fake_engram, capsys):
    from piia_engram.watcher.__main__ import main

    day = _day_dir(codex_adapter.sessions_root())
    _write_rollout(day, turns=[("user", "hello")])
    assert main(["--once"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert set(out) == {"discovered", "saved", "skipped", "baselined", "errors"}


def test_cli_adapter_filter(fake_engram, capsys):
    from piia_engram.watcher.__main__ import main

    assert main(["--once", "--adapters", "codex"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["errors"] == 0


# ---------------------------------------------------------------------------
# Autostart install (engram watcher install/uninstall/status)
# ---------------------------------------------------------------------------


@pytest.fixture()
def install_env(monkeypatch, tmp_path: Path):
    """Force the win32 code path with APPDATA isolated and shortcuts stubbed."""
    import sys as _sys

    from piia_engram.watcher import install as winstall

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(_sys, "platform", "win32")
    shortcuts: list[tuple] = []
    monkeypatch.setattr(
        winstall, "_create_shortcut", lambda *a, **kw: shortcuts.append(a)
    )
    return SimpleNamespace(mod=winstall, shortcuts=shortcuts)


def test_launcher_source_bakes_paths_and_interval():
    from piia_engram.watcher import install as winstall

    src = winstall.build_launcher_source(45.0)
    assert "ENGRAM_DIR" in src
    assert repr(str(winstall.engram_dir())) in src  # repr-escaped in source
    assert "'--interval', '45'" in src
    assert "piia_engram.watcher.__main__" in src
    compile(src, "<launcher>", "exec")  # must be valid python


def test_install_writes_launcher_and_shortcut(install_env, capsys):
    assert install_env.mod.install(60.0) == 0
    launcher = install_env.mod.launcher_path()
    assert launcher.exists()
    assert "Auto-generated" in launcher.read_text(encoding="utf-8")
    assert len(install_env.shortcuts) == 1
    assert install_env.shortcuts[0][0] == install_env.mod.shortcut_path()
    assert "installed" in capsys.readouterr().out


def test_install_shortcut_failure_keeps_launcher_and_fails(
    install_env, monkeypatch, capsys
):
    def _boom(*_a, **_kw):
        raise OSError("no powershell")

    monkeypatch.setattr(install_env.mod, "_create_shortcut", _boom)
    assert install_env.mod.install(60.0) == 1
    assert install_env.mod.launcher_path().exists()
    assert "failed" in capsys.readouterr().out


def test_uninstall_removes_only_install_artifacts(install_env, capsys):
    install_env.mod.install(60.0)
    # Simulate the shortcut existing on disk (creation is stubbed).
    lnk = install_env.mod.shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    lnk.write_text("stub", encoding="utf-8")
    state = core._state_dir() / "watcher_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")

    assert install_env.mod.uninstall() == 0
    assert not lnk.exists()
    assert not install_env.mod.launcher_path().exists()
    assert state.exists()  # state/logs are never deleted
    assert "removed" in capsys.readouterr().out


def test_uninstall_when_nothing_installed(install_env, capsys):
    assert install_env.mod.uninstall() == 0
    assert "nothing to remove" in capsys.readouterr().out


def test_status_reports_install_and_scan_state(install_env, capsys):
    assert install_env.mod.status() == 0
    out = capsys.readouterr().out
    assert "autostart shortcut: no" in out
    assert "never" in out

    install_env.mod.install(60.0)
    state = core._state_dir() / "watcher_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")
    capsys.readouterr()
    assert install_env.mod.status() == 0
    out = capsys.readouterr().out
    assert "launcher          : yes" in out
    assert "last scan         : 2" in out  # ISO year prefix


def test_install_posix_prints_guidance_writes_nothing(monkeypatch, capsys):
    import sys as _sys

    from piia_engram.watcher import install as winstall

    monkeypatch.setattr(_sys, "platform", "linux")
    assert winstall.install(60.0) == 0
    assert not winstall.launcher_path().exists()
    assert "cron" in capsys.readouterr().out


def test_watcher_cli_routing(install_env, capsys):
    assert install_env.mod.run_watcher_cli([]) == 2
    assert install_env.mod.run_watcher_cli(["bogus"]) == 2
    capsys.readouterr()
    assert install_env.mod.run_watcher_cli(["status"]) == 0
    assert install_env.mod.run_watcher_cli(["install", "--interval", "bad"]) == 2
    capsys.readouterr()
    assert install_env.mod.run_watcher_cli(["install", "--interval", "30"]) == 0
    assert "30s" in capsys.readouterr().out


def test_watcher_cli_once_routes_to_scanner(install_env, fake_engram, capsys):
    assert install_env.mod.run_watcher_cli(["once"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert "discovered" in out
