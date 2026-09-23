"""Pre-push guard: check the refs a push is about to update.

Git runs a pre-push hook with the remote name and URL as arguments and one line
per ref update on stdin::

    <local ref> <local sha> <remote ref> <remote sha>

This guard refuses the push when:

- a tag points at a commit that will not be reachable from the remote's main
  after the push (a bulk tag push would otherwise publish local-only tags);
- a ref is deleted (set ENGRAM_ALLOW_REF_DELETE=1 for a deliberate delete);
- a commit message or annotated tag message about to be pushed matches the
  scanner patterns of ``scripts/release_sanitize_check.py``, including the
  maintainer's local, gitignored term lists.

The term lists stay outside the repository. In a linked worktree the
``.sanitizeignore`` of the main checkout is used when the worktree has none.

Wire it from ``.git/hooks/pre-push``::

    python scripts/check_push_refs.py "$@" || exit 1

Exit code: 0 when every update passes, 1 otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ZERO_SHA = "0" * 40
_SANITIZE_SCRIPT = Path(__file__).resolve().with_name("release_sanitize_check.py")


def _load_sanitize():
    spec = importlib.util.spec_from_file_location("_release_sanitize_check", _SANITIZE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def _git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def _patterns(sanitize):
    patterns = list(sanitize._BUILT_IN_PATTERNS) + list(sanitize._INTERNAL_DISCLOSURE_PATTERNS)
    patterns += sanitize._load_internal_patterns_file()
    return patterns, sanitize._load_custom_terms()


def _commits_to_push(local_sha: str, remote_sha: str, remote: str) -> list[str]:
    if remote_sha != ZERO_SHA and _git_ok("cat-file", "-e", f"{remote_sha}^{{commit}}"):
        rev_args = [f"{remote_sha}..{local_sha}"]
    else:
        rev_args = [local_sha, "--not", f"--remotes={remote}"]
    out = _git("rev-list", *rev_args)
    return out.split() if out else []


def _main_after_push(updates: list[tuple[str, str, str, str]], remote: str) -> str | None:
    for _local_ref, local_sha, remote_ref, _remote_sha in updates:
        if remote_ref == "refs/heads/main" and local_sha != ZERO_SHA:
            return local_sha
    tracking = f"refs/remotes/{remote}/main"
    return _git("rev-parse", tracking) if _git_ok("rev-parse", "--verify", "-q", tracking) else None


def check_updates(lines: list[str], remote: str) -> list[str]:
    """Return a list of problems for the given pre-push stdin lines."""
    updates = [tuple(line.split()) for line in lines if line.strip()]
    updates = [u for u in updates if len(u) == 4]
    sanitize = _load_sanitize()
    patterns, custom = _patterns(sanitize)
    main_after = _main_after_push(updates, remote)
    problems: list[str] = []

    for local_ref, local_sha, remote_ref, remote_sha in updates:
        if local_sha == ZERO_SHA:
            if os.environ.get("ENGRAM_ALLOW_REF_DELETE") != "1":
                problems.append(f"{remote_ref}: refusing to delete a remote ref")
            continue

        if remote_ref.startswith("refs/tags/"):
            tag = remote_ref[len("refs/tags/"):]
            commit = _git("rev-parse", f"{local_sha}^{{commit}}")
            if main_after is None:
                problems.append(f"tag {tag}: cannot check reachability, {remote}/main is unknown")
            elif not _git_ok("merge-base", "--is-ancestor", commit, main_after):
                problems.append(
                    f"tag {tag}: commit {commit[:10]} is not reachable from {remote} main"
                )
            if _git("cat-file", "-t", local_sha) == "tag":
                message = _git("cat-file", "-p", local_sha).split("\n\n", 1)[-1]
                for _name, label, _sev, snippet in sanitize._scan_message_text(
                    tag, message, custom, patterns
                ):
                    problems.append(f"tag {tag} message: {label}: {snippet}")
            continue

        for sha in _commits_to_push(local_sha, remote_sha, remote):
            message = _git("log", "-1", "--format=%B", sha)
            for _name, label, _sev, snippet in sanitize._scan_message_text(
                sha[:10], message, custom, patterns
            ):
                problems.append(f"commit {sha[:10]} message: {label}: {snippet}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    remote = args[0] if args else "origin"
    problems = check_updates(sys.stdin.read().splitlines(), remote)
    for problem in problems:
        print(f"[push-guard] {problem}", file=sys.stderr)
    if problems:
        print("[push-guard] push refused; fix the refs or messages above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
