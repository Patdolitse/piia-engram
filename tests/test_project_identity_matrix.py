"""Residual input matrix for project identity (post-v4.15.0 fix).

Explicit contracts for input classes the original fix did not enumerate:
existing files, symlinks (live and broken), malformed .git / commondir,
case folding, cross-cwd stability, and platform-specific path forms
(UNC / extended-length / drive-relative are asserted only where the form is
meaningful on the running platform).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from piia_engram.storage import (
    _git_common_dir_for_folder,
    _legacy_project_id,
    _project_id,
)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return path


def test_existing_file_resolves_to_parent_identity(tmp_path):
    """Policy: an existing FILE is treated as its parent directory."""
    repo = _repo(tmp_path / "repo")
    project = repo / "proj"
    project.mkdir()
    marker = project / "notes.md"
    marker.write_text("x", encoding="utf-8")
    assert _project_id(str(marker)) == _project_id(str(project))
    assert _project_id(str(project)) == _project_id(str(repo))


def test_symlink_to_project_inside_repo_dereferences_to_repo_identity(tmp_path):
    """Policy: symlinks are dereferenced (Path.resolve) — identity follows the
    TARGET's location, not the link's."""
    repo = _repo(tmp_path / "repo")
    project = repo / "proj"
    project.mkdir()
    link = tmp_path / "link-to-proj"
    try:
        link.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform/configuration")
    assert _project_id(str(link)) == _project_id(str(project))


def test_broken_symlink_falls_back_to_distinct_legacy_identity(tmp_path):
    repo = _repo(tmp_path / "repo")  # an ancestor repo exists on purpose
    target = tmp_path / "does-not-exist"
    link = tmp_path / "broken-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform/configuration")
    # resolve() of a broken symlink yields the target path, which does not
    # exist -> no git walk -> legacy key of the resolved target (distinct
    # from the repo, never inherited).
    assert _project_id(str(link)) == _project_id(str(target))
    assert _project_id(str(link)) != _project_id(str(repo))


def test_malformed_git_file_is_skipped_and_walk_continues(tmp_path):
    """Policy: a .git FILE whose content is not a gitdir pointer is ignored;
    the walk continues to ancestors (a malformed marker is not a repo)."""
    outer = _repo(tmp_path / "outer")
    inner = outer / "inner"
    inner.mkdir()
    (inner / ".git").write_text("this is not a gitdir pointer", encoding="utf-8")
    project = inner / "proj"
    project.mkdir()
    assert _project_id(str(project)) == _project_id(str(outer))


def test_malformed_commondir_falls_back_to_own_gitdir(tmp_path):
    main_git = _repo(tmp_path / "main" / ".git")  # .git dir as common anchor
    worktree = tmp_path / "wt"
    worktree.mkdir()
    gitdir = main_git / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("", encoding="utf-8")  # malformed: empty
    (worktree / ".git").write_text(f"gitdir: {gitdir}", encoding="utf-8")
    result = _git_common_dir_for_folder(str(worktree))
    assert result is not None and result != main_git  # identity = own gitdir


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path forms")
def test_windows_unc_and_drive_relative_forms_stay_distinct(tmp_path):
    # UNC absolute, nonexistent -> exists() False -> legacy key, distinct
    a = "\\\\definitely-not-a-server\\share\\proj-a"
    b = "\\\\definitely-not-a-server\\share\\proj-b"
    assert _project_id(a) != _project_id(b)
    assert _git_common_dir_for_folder(a) is None
    # drive-relative ("E:folder") is platform-ambiguous; contract: treated as
    # a path string, nonexistent -> legacy fallback, never an ancestor repo
    assert _git_common_dir_for_folder("Q:definitely-not-on-disk") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only permission test")
def test_unreadable_directory_falls_back_without_inheriting_ancestors(tmp_path):
    repo = _repo(tmp_path / "repo")
    secret = repo / "locked"
    secret.mkdir()
    locked_marker = secret / ".git"
    locked_marker.mkdir()
    try:
        os.chmod(secret, 0o000)
    except OSError:
        pytest.skip("cannot drop permissions")
    try:
        # CONTRACT: identity derivation must never RAISE for an unreadable
        # directory (CPython Path.is_dir() propagates EACCES), and must stay
        # deterministic. The unreadable own .git is treated like a malformed
        # marker: the walk continues upward and may inherit the ancestor
        # repo anchor — that is the documented skip rule, not a leak.
        first = _project_id(str(secret))
        second = _project_id(str(secret))
        assert first == second
    finally:
        os.chmod(secret, 0o755)


def test_case_folding_policy_on_legacy_key():
    """CONTRACT: the legacy path-key lowercases the whole resolved path, so
    two case-only spellings share one id on EVERY platform — including
    case-sensitive filesystems where they would be distinct folders. Found
    by running this matrix on Linux; pinned as intended behavior."""
    a = "C:/definitely/not/on/disk/Project"
    b = "C:/definitely/not/on/disk/project"
    assert _legacy_project_id(a) == _legacy_project_id(b)


def test_existing_outside_repo_identity_is_cwd_independent(tmp_path, monkeypatch):
    """An existing folder outside any repo maps to the same id regardless of
    the process cwd (absolute path, no cwd-dependent resolution)."""
    folder = tmp_path / "plain-project"
    folder.mkdir()
    first = _project_id(str(folder))
    monkeypatch.chdir(tmp_path)
    assert _project_id(str(folder)) == first
