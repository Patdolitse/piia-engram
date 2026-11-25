"""The resume brief's session cap counts sessions that have a digest.

The digest walk looks at a bounded number of sessions so a brief does not
grow with the store. The bound used to count every session file, and most
files in a real store are short records without a digest: a project not
touched for a few days fell behind 200 of them and its brief came without
the last session. Records without a digest no longer use up the bound.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from piia_engram import contexts as contexts_mod
from piia_engram.core import Engram

STUB = "# Session: claude_code @ 2026-01-01 10:00\n\n### 10:00\nshort session\n"
BASE = 1_700_000_000


def _checkpoint(eng, tool, session_id, folder, mtime) -> Path:
    saved = eng.save_agent_context(
        tool, f"Goal: finish {session_id}.\nNext: assemble {session_id}.\n",
        session_id=session_id, project_folder=folder)
    os.utime(saved["file"], (mtime, mtime))
    return Path(saved["file"])


def _stubs(eng, tool, count, first_mtime) -> None:
    tool_dir = eng._contexts_dir / tool
    tool_dir.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        path = tool_dir / f"stub-{index:05d}.md"
        path.write_text(STUB, encoding="utf-8")
        os.utime(path, (first_mtime + index, first_mtime + index))


def _found(eng, project, limit=6) -> list[str]:
    return [d["source"]["session_ref"]
            for d in eng._recent_session_digests(project_folder=str(project), limit=limit)]


@pytest.fixture
def world(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return Engram(root=tmp_path / "store"), project


def test_records_without_a_digest_do_not_use_up_the_cap(world):
    eng, project = world
    limit = contexts_mod.RESUME_DIGEST_SCAN_LIMIT
    _checkpoint(eng, "claude_code", "last-week", str(project), BASE)
    # Three days of other work: far more short records than the cap, all newer,
    # in the same tool directory and in another one.
    _stubs(eng, "claude_code", limit + 50, BASE + 1_000)
    _stubs(eng, "codex", limit + 50, BASE + 5_000)

    assert _found(eng, project) == ["last-week"]

    brief = eng.get_resume_brief(project_folder=str(project), token_budget=1500)
    assert "assemble last-week" in brief["markdown"]


def test_the_cap_counts_sessions_with_a_digest(world, monkeypatch):
    eng, project = world
    other = project.parent / "other"
    other.mkdir()
    monkeypatch.setattr(contexts_mod, "RESUME_DIGEST_SCAN_LIMIT", 3)
    _checkpoint(eng, "claude_code", "mine", str(project), BASE)
    _stubs(eng, "claude_code", 10, BASE + 100)
    _checkpoint(eng, "codex", "other-1", str(other), BASE + 1_000)
    _checkpoint(eng, "codex", "other-2", str(other), BASE + 1_001)

    # mine is the third session with a digest: the last one looked at.
    assert _found(eng, project) == ["mine"]

    _checkpoint(eng, "codex", "other-3", str(other), BASE + 1_002)

    # Three newer sessions with a digest: mine is behind the cap now, and the
    # digest itself is still readable.
    assert _found(eng, project) == []
    assert eng.get_session_digest("claude_code", "mine")["source"]["session_ref"] == "mine"


def test_newest_first_across_tools_is_unchanged(world):
    eng, project = world
    _checkpoint(eng, "claude_code", "a", str(project), BASE + 1)
    _checkpoint(eng, "codex", "b", str(project), BASE + 3)
    _checkpoint(eng, "claude_code", "c", str(project), BASE + 2)
    _stubs(eng, "claude_code", 5, BASE + 10)

    assert _found(eng, project) == ["b", "c", "a"]
    assert _found(eng, project, limit=1) == ["b"]


def test_each_directory_is_listed_once_and_stubs_are_not_examined(world, monkeypatch):
    eng, project = world
    _checkpoint(eng, "claude_code", "a", str(project), BASE)
    _checkpoint(eng, "codex", "b", str(project), BASE + 1)
    _stubs(eng, "claude_code", 300, BASE + 100)
    listed: list[str] = []
    examined: list[str] = []
    real_listdir, real_stat, real_is_file = os.listdir, Path.stat, Path.is_file

    def listdir(path="."):
        listed.append(Path(path).name)
        return real_listdir(path)

    def stat(self, *args, **kwargs):
        examined.append(self.name)
        return real_stat(self, *args, **kwargs)

    def is_file(self):
        examined.append(self.name)
        return real_is_file(self)

    monkeypatch.setattr(contexts_mod.os, "listdir", listdir)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "is_file", is_file)

    assert _found(eng, project) == ["b", "a"]

    assert listed.count("claude_code") == 1 and listed.count("codex") == 1
    assert not [name for name in examined if name.startswith("stub-")]


def test_a_sidecar_that_is_not_a_file_is_skipped(world):
    eng, project = world
    _checkpoint(eng, "claude_code", "a", str(project), BASE)
    tool_dir = eng._contexts_dir / "claude_code"
    (tool_dir / "fake.md").write_text(STUB, encoding="utf-8")
    (tool_dir / "fake.digest.json").mkdir()

    assert _found(eng, project) == ["a"]
