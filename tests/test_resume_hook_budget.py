"""Issue #91: the SessionStart resume-hook budget is usable, bounded and visible.

``hooks/_budget.py`` gives the resume read a default budget, an environment
override clamped below the hook's registered host timeout, and a timeout path
that still answers the host with ``{"continue": true}`` and exit status 0.
"""

from __future__ import annotations

import io
import json
import sys
import time
from importlib import import_module

import pytest

from piia_engram import core
from piia_engram.hooks import _budget

HOOKS = ("auto_inject_resume_brief", "cursor_inject_resume_brief")
HOST_MARGIN = _budget.HOST_MARGIN_SECONDS
SESSIONSTART = "auto_inject_resume_brief"


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv(_budget.BUDGET_ENV, raising=False)


def test_default_budget_fits_each_registered_hook():
    assert _budget.READ_BUDGET_SECONDS == 10.0
    assert _budget.MIN_BUDGET_SECONDS == 1.0
    assert set(_budget.HOOK_TIMEOUT_SECONDS) == set(HOOKS)
    for hook in HOOKS:
        host_timeout = _budget.HOOK_TIMEOUT_SECONDS[hook]
        assert _budget.MIN_BUDGET_SECONDS < _budget.READ_BUDGET_SECONDS < host_timeout - HOST_MARGIN
        assert _budget.budget_seconds(hook) == 10.0


def test_budget_table_matches_the_timeout_sessionstart_is_registered_with(tmp_path, monkeypatch):
    from piia_engram.claude_code_mcp import settings_path
    from piia_engram.setup_wizard import _inject_claude_code_sessionstart_hook

    monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
    settings_path().parent.mkdir(parents=True, exist_ok=True)

    assert _inject_claude_code_sessionstart_hook(sys.executable) is not None

    settings = json.loads(settings_path().read_text(encoding="utf-8"))
    hooks = [hook for group in settings["hooks"]["SessionStart"] for hook in group["hooks"]]
    (hook,) = [hook for hook in hooks
               if "piia_engram.hooks.auto_inject_resume_brief" in hook["command"]]
    assert hook["timeout"] == _budget.HOOK_TIMEOUT_SECONDS[SESSIONSTART] == 15


@pytest.mark.parametrize("raw,expected", [
    ("12.5", 12.5),
    (" 7 ", 7.0),
    ("1e1", 10.0),
    ("14", 14.0),  # the registered 15 s timeout less the host margin
    ("15", 14.0),
    ("999", 14.0),
    ("0.2", 1.0),  # the floor
    ("0", 1.0),
    ("-3", 1.0),
    ("1e-400", 1.0),
    ("", 10.0),  # blank: the default
    ("abc", 10.0),  # invalid: the default
    ("10s", 10.0),
    ("nan", 10.0),
    ("inf", 10.0),
    ("1e400", 10.0),
])
def test_override_is_parsed_and_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv(_budget.BUDGET_ENV, raw)

    assert _budget.budget_seconds(SESSIONSTART) == expected


@pytest.mark.parametrize("hook", HOOKS)
def test_each_hook_is_clamped_below_its_own_registered_timeout(monkeypatch, hook):
    monkeypatch.setenv(_budget.BUDGET_ENV, "30")

    assert _budget.budget_seconds(hook) == _budget.HOOK_TIMEOUT_SECONDS[hook] - HOST_MARGIN

    table = dict(_budget.HOOK_TIMEOUT_SECONDS, **{hook: 4.5})
    monkeypatch.setattr(_budget, "HOOK_TIMEOUT_SECONDS", table)

    assert _budget.budget_seconds(hook) == 3.5
    monkeypatch.delenv(_budget.BUDGET_ENV)
    assert _budget.budget_seconds(hook) == 3.5  # the default stays under the host timeout too


def test_an_unknown_hook_has_no_upper_bound(monkeypatch):
    monkeypatch.setenv(_budget.BUDGET_ENV, "30")

    assert _budget.budget_seconds("some_other_hook") == 30.0


def test_an_invalid_or_missing_override_uses_the_module_default(monkeypatch):
    monkeypatch.setattr(_budget, "READ_BUDGET_SECONDS", 2.5)

    assert _budget.budget_seconds(SESSIONSTART) == 2.5
    for raw in ("", "soon", "inf"):
        monkeypatch.setenv(_budget.BUDGET_ENV, raw)
        assert _budget.budget_seconds(SESSIONSTART) == 2.5
    monkeypatch.setenv(_budget.BUDGET_ENV, "5")
    assert _budget.budget_seconds(SESSIONSTART) == 5.0


def test_read_with_budget_runs_with_the_clamped_override(monkeypatch):
    monkeypatch.setenv(_budget.BUDGET_ENV, "0.2")  # without the floor this read would time out

    started = time.monotonic()
    assert _budget.read_with_budget(lambda: "brief", SESSIONSTART) == "brief"
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("module", HOOKS)
def test_timeout_with_an_override_still_continues_and_logs(tmp_path, monkeypatch, capsys, module):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv(_budget.BUDGET_ENV, "1")
    monkeypatch.delenv("CLAUDE_INVOKED_BY", raising=False)

    class SlowEngram:
        def __init__(self, *, read_only):
            assert read_only is True

        def get_resume_brief(self, **kwargs):
            time.sleep(5)
            return {"markdown": "late"}

    monkeypatch.setattr(core, "Engram", SlowEngram)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(sys, "argv", ["hook"])
    started = time.monotonic()

    assert import_module("piia_engram.hooks." + module).main() == 0

    elapsed = time.monotonic() - started
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    # Engram gave up at the clamped 1 s budget, well before the registered 15 s.
    assert 0.9 <= elapsed < 4
    log = (tmp_path / "store" / "logs" / "hooks.log").read_text(encoding="utf-8")
    assert f"[{module}] resume read budget exceeded" in log
