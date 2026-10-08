"""Directory links cannot redirect caller-selected store paths."""

import json
import os
import subprocess

import pytest

from piia_engram.core import Engram


def _directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name == "nt":
            result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True)
            if result.returncode == 0:
                return
        pytest.skip("directory links unavailable")


def test_context_tool_link_cannot_write_outside_contexts(tmp_path):
    eng = Engram(root=tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    contexts = eng.root / "contexts"
    contexts.mkdir(exist_ok=True)
    _directory_link(contexts / "linked", outside)
    with pytest.raises(ValueError):
        eng.save_agent_context("linked", "Goal: no escaped write", session_id="probe")
    assert list(outside.iterdir()) == []


def test_playbook_execution_link_cannot_write_outside_playbooks(tmp_path):
    eng = Engram(root=tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    _directory_link(eng.root / "playbooks" / "executions", outside)
    with pytest.raises(ValueError):
        eng._execution_path("safe-id")
    assert list(outside.iterdir()) == []


def test_daily_log_link_cannot_read_outside_daily(tmp_path):
    from piia_engram.storage import _project_id

    eng = Engram(root=tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "2026-10-07.md").write_text("private marker", encoding="utf-8")
    daily = eng.root / "daily"
    daily.mkdir(exist_ok=True)
    _directory_link(daily / _project_id(""), outside)
    result = eng.get_daily_log("", date="2026-10-07")
    assert not result.get("content"), result


def test_playbook_file_link_cannot_read_outside_playbooks(tmp_path):
    eng = Engram(root=tmp_path / "store")
    outside = tmp_path / "external.json"
    outside.write_text(json.dumps({"id": "linked", "title": "private marker", "steps": []}),
                       encoding="utf-8")
    try:
        (eng.root / "playbooks" / "linked.json").symlink_to(outside)
    except OSError:
        pytest.skip("file links unavailable")
    assert eng._read_playbook_by_id("linked") is None


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("bad_id", ["../identity/profile", "..\\identity\\profile"])
def test_backup_project_ids_never_overwrite_identity(tmp_path, dry_run, bad_id):
    eng = Engram(root=tmp_path / "store")
    eng.update_profile({"role": "original role"})
    before = (eng.root / "identity" / "profile.json").read_bytes()
    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"schema_version": "2.0", "projects": {
        bad_id: {"role": "replacement role"}}}), encoding="utf-8")
    result = eng.import_all(str(backup), merge=False, dry_run=dry_run)
    assert result.get("error") == "invalid_project_id", result
    assert (eng.root / "identity" / "profile.json").read_bytes() == before
