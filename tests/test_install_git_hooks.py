"""Tests for scripts/install_git_hooks.py (v3.32).

Loads the script module by path (scripts/ is not an installed package)
and checks the generated hook body + marker-upgrade logic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "install_git_hooks.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_install_git_hooks", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ih():
    return _load_module()


def test_hook_body_runs_both_checks(ih):
    """v3.32: the pre-commit hook runs sanitize AND the publish allowlist."""
    body = ih.HOOK_BODY
    assert "release_sanitize_check.py" in body
    assert "check_publish_allowlist.py" in body


def test_hook_body_aggregates_exit_codes(ih):
    """A failure in either check must block the commit (rc=1), not let the
    second check's success mask the first's failure."""
    body = ih.HOOK_BODY
    # both checks set rc on failure, and the final gate gates on rc
    assert body.count("rc=1") >= 2
    assert 'exit 1' in body


def test_current_marker_is_v4(ih):
    assert ih.HOOK_MARKER == "# piia-engram-sanitize-hook v4"
    assert ih.HOOK_MARKER in ih.HOOK_BODY


def test_hook_validates_python_before_use(ih):
    # The hook must not blindly run the first `python` on PATH (e.g. the Windows
    # Store alias stub, which exits non-zero without running code and would
    # falsely block every commit). It validates each candidate with a no-op.
    assert "_works" in ih.HOOK_BODY
    assert "import sys" in ih.HOOK_BODY


def test_old_v1_marker_recognized_for_upgrade(ih):
    """Installing over a v1 hook must be treated as 'ours' so it upgrades
    cleanly instead of refusing."""
    assert "# piia-engram-sanitize-hook v1" in ih._KNOWN_MARKERS
    assert ih.HOOK_MARKER in ih._KNOWN_MARKERS


def test_install_upgrades_existing_v1_hook(ih, tmp_path, monkeypatch):
    """End-to-end: a pre-existing v1 hook gets overwritten with the v2
    body that includes the allowlist check."""
    git_dir = tmp_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    hook = git_dir / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n# piia-engram-sanitize-hook v1\n"
        "python scripts/release_sanitize_check.py --staged\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ih, "_git_dir", lambda: git_dir)
    assert ih.install() == 0
    new_body = hook.read_text(encoding="utf-8")
    assert "# piia-engram-sanitize-hook v4" in new_body
    assert "check_publish_allowlist.py --staged" in new_body


def test_install_refuses_foreign_hook(ih, tmp_path, monkeypatch):
    """A pre-commit hook we didn't write must not be clobbered."""
    git_dir = tmp_path / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    hook = git_dir / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho someone elses hook\n", encoding="utf-8")
    monkeypatch.setattr(ih, "_git_dir", lambda: git_dir)
    assert ih.install() == 1
    # untouched
    assert "someone elses hook" in hook.read_text(encoding="utf-8")
