"""Tests for the offline context-governance eval harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_context_governance.py"


def _load():
    spec = importlib.util.spec_from_file_location("_eval_context_governance", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_context_governance_eval_is_green_and_synthetic():
    mod = _load()
    result = mod.run_eval()

    assert result["ok"] is True
    assert result["fixture_count"] == result["mode_count"]
    assert result["applied_false"] == result["mode_count"]
    assert result["secret_redacted"] is True
    assert "no real store read" in result["note"]


def test_context_governance_eval_main_returns_zero(capsys):
    mod = _load()

    assert mod.main([]) == 0
    assert "context-governance eval" in capsys.readouterr().out
