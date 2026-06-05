from __future__ import annotations

from piia_engram.hooks.writeback_policy import check_writeback_allowed


def test_writeback_policy_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK", raising=False)
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert check_writeback_allowed("ENGRAM_CURSOR_WRITEBACK", staging_gate=True) is False


def test_writeback_policy_allows_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert check_writeback_allowed("ENGRAM_CURSOR_WRITEBACK", staging_gate=True) is True


def test_writeback_policy_requires_staging_gate(monkeypatch):
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert check_writeback_allowed("ENGRAM_CURSOR_WRITEBACK", staging_gate=False) is False


def test_writeback_policy_blocks_reentry(monkeypatch):
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", "1")

    assert check_writeback_allowed("ENGRAM_CURSOR_WRITEBACK", staging_gate=True) is False
