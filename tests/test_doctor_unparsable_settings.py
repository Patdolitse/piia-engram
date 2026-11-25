"""Doctor and a Claude Code ``settings.json`` it cannot parse.

A hand-edited ``settings.json`` with a JSON error (typically an unescaped
Windows path) made doctor report every Engram hook as missing, and
``--fix`` answer "already up to date" next to a logged "Failed to
register". Doctor now says once that the file cannot be parsed, where, and
leaves it exactly as it is.

Every ``settings.json`` here is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor

PY = "/usr/bin/python3"
# An unescaped Windows path: "Invalid \escape" on the second line.
BROKEN = '{\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "C:\\Users\\x"}]}]}\n}\n'


@pytest.fixture
def run_doctor(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setattr(W, "_find_python", lambda: PY)

    def run(*, fix: bool) -> str:
        capsys.readouterr()
        doctor._run_functional_checks(fix=fix)
        return capsys.readouterr().out

    run.settings = config / "settings.json"
    return run


@pytest.mark.parametrize("fix", [False, True])
def test_json_error_is_reported_once_with_its_place(run_doctor, fix):
    run_doctor.settings.write_text(BROKEN, encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(BROKEN)
    before = run_doctor.settings.read_bytes()

    out = run_doctor(fix=fix)

    assert out.count("[!!] Claude Code settings cannot be parsed") == 1
    assert str(run_doctor.settings) in out
    assert "line 2, column" in out
    # Nothing is said about hooks that could not be looked at.
    assert "No Engram" not in out
    assert "already up to date" not in out
    assert "[fixed]" not in out.split("-- Claude Code Hooks --")[1].split("-- Encoding health --")[0]
    # The owner's file is never overwritten, and no copy appears next to it.
    assert run_doctor.settings.read_bytes() == before
    names = [p.name for p in run_doctor.settings.parent.iterdir()]
    assert [name for name in names if "settings" in name] == ["settings.json"]


@pytest.mark.parametrize("content, reason", [
    ("[]", "not a JSON object"),
    ('"text"', "not a JSON object"),
    ('{"hooks": {"Stop": ', "line 1, column"),
])
def test_other_unusable_content(run_doctor, content, reason):
    run_doctor.settings.write_text(content, encoding="utf-8")

    out = run_doctor(fix=True)

    assert "[!!] Claude Code settings cannot be parsed" in out and reason in out
    assert "No Engram" not in out
    assert run_doctor.settings.read_text(encoding="utf-8") == content


def test_undecodable_bytes(run_doctor):
    run_doctor.settings.write_bytes(b'{"hooks": "\xff\xfe"}')

    out = run_doctor(fix=True)

    assert "[!!] Claude Code settings cannot be parsed" in out
    assert run_doctor.settings.read_bytes() == b'{"hooks": "\xff\xfe"}'


def test_valid_settings_are_checked_as_before(run_doctor):
    run_doctor.settings.write_text("{}", encoding="utf-8")

    out = run_doctor(fix=False)
    assert "cannot be parsed" not in out
    assert "[--] No Engram Stop hook in Claude Code settings" in out

    out = run_doctor(fix=True)
    assert "[fixed] Stop hook registered" in out


def test_missing_settings_are_checked_as_before(run_doctor):
    out = run_doctor(fix=False)
    assert "cannot be parsed" not in out
    assert "[--] No Engram Stop hook in Claude Code settings" in out
