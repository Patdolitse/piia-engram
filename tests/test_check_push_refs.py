"""Tests for scripts/check_push_refs.py, the local pre-push guard.

The guard reads the ref updates git passes to a pre-push hook and refuses:
- a tag whose commit is not reachable from the remote's main after the push;
- a ref deletion;
- a pushed commit message or annotated tag message that matches a private
  term from the local, gitignored pattern lists.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_push_refs.py"
_ZERO = "0" * 40
_CANARY = "PRIVATE_CANARY_" + "DO_NOT_RELEASE"


@pytest.fixture(scope="module")
def guard():
    assert _SCRIPT.is_file(), "scripts/check_push_refs.py is missing"
    spec = importlib.util.spec_from_file_location("check_push_refs", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    return proc.stdout.strip()


def _commit(repo: Path, name: str, message: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A clone whose origin/main is at `base`; local main is one commit ahead."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "checkout", "-q", "-b", "main")
    base = _commit(repo, "base.txt", "chore: base")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "--no-verify", "origin", "main:refs/heads/main")
    _git(repo, "fetch", "-q", "origin")
    head = _commit(repo, "next.txt", "fix: next")
    (repo / ".sanitizeignore").write_text(f"high:{_CANARY}\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(repo))
    monkeypatch.setenv("USERPROFILE", str(repo))
    monkeypatch.delenv("ENGRAM_ALLOW_REF_DELETE", raising=False)
    return repo, base, head


def _line(local_ref: str, local_sha: str, remote_ref: str, remote_sha: str = _ZERO) -> str:
    return f"{local_ref} {local_sha} {remote_ref} {remote_sha}"


def test_main_fast_forward_with_its_own_tag_is_allowed(guard, clone):
    repo, base, head = clone
    _git(repo, "tag", "v1.0.0", head)
    lines = [
        _line("refs/heads/main", head, "refs/heads/main", base),
        _line("refs/tags/v1.0.0", head, "refs/tags/v1.0.0"),
    ]
    assert guard.check_updates(lines, remote="origin") == []


def test_tag_not_reachable_from_main_is_refused(guard, clone):
    repo, base, _head = clone
    _git(repo, "checkout", "-q", "-b", "side", base)
    side = _commit(repo, "side.txt", "chore: side work")
    _git(repo, "tag", "v0.9.9-local", side)
    problems = guard.check_updates(
        [_line("refs/tags/v0.9.9-local", side, "refs/tags/v0.9.9-local")], remote="origin"
    )
    assert any("v0.9.9-local" in p and "not reachable" in p for p in problems), problems


def test_ref_deletion_is_refused(guard, clone):
    _repo, base, _head = clone
    problems = guard.check_updates(
        [_line("(delete)", _ZERO, "refs/heads/old", base)], remote="origin"
    )
    assert any("delete" in p for p in problems), problems


def test_private_term_in_pushed_commit_message_is_refused(guard, clone):
    repo, base, _head = clone
    bad = _commit(repo, "bad.txt", f"fix: mentions {_CANARY}")
    problems = guard.check_updates(
        [_line("refs/heads/main", bad, "refs/heads/main", base)], remote="origin"
    )
    assert any(bad[:10] in p for p in problems), problems


def test_private_term_in_annotated_tag_message_is_refused(guard, clone):
    repo, base, head = clone
    _git(repo, "tag", "-a", "v1.0.1", head, "-m", f"release {_CANARY}")
    lines = [
        _line("refs/heads/main", head, "refs/heads/main", base),
        _line("refs/tags/v1.0.1", _git(repo, "rev-parse", "v1.0.1"), "refs/tags/v1.0.1"),
    ]
    problems = guard.check_updates(lines, remote="origin")
    assert any("v1.0.1" in p for p in problems), problems


def test_main_reads_stdin_and_exits_one_on_a_problem(guard, clone, monkeypatch):
    import io

    repo, base, _head = clone
    _git(repo, "checkout", "-q", "-b", "side", base)
    side = _commit(repo, "side.txt", "chore: side work")
    _git(repo, "tag", "v0.9.9-local", side)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        _line("refs/tags/v0.9.9-local", side, "refs/tags/v0.9.9-local") + "\n"
    ))
    assert guard.main(["origin", "unused-url"]) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert guard.main(["origin", "unused-url"]) == 0


def test_linked_worktree_uses_the_main_checkout_term_list(guard, clone, monkeypatch, tmp_path):
    repo, base, _head = clone
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(linked), "main")
    bad = _commit(linked, "bad.txt", f"fix: mentions {_CANARY}")
    assert not (linked / ".sanitizeignore").exists()
    monkeypatch.chdir(linked)
    problems = guard.check_updates(
        [_line("refs/heads/feature", bad, "refs/heads/feature")], remote="origin"
    )
    assert any(bad[:10] in p for p in problems), problems
