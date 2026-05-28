"""Tests for scripts/check_publish_allowlist.py (v3.31 publish whitelist).

Covers the matching semantics and the real-repo invariant that every
tracked file is currently covered by .publishallow.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_publish_allowlist.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_check_allowlist", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cl():
    return _load_module()


# ── matching semantics ──────────────────────────────────────────────


def test_subtree_glob_matches_nested(cl):
    assert cl._matches("src/piia_engram/core.py", "src/**")
    assert cl._matches("src/a/b/c/deep.py", "src/**")
    assert not cl._matches("srcfoo/x.py", "src/**")  # prefix must include slash


def test_exact_path_match(cl):
    assert cl._matches("pyproject.toml", "pyproject.toml")
    assert not cl._matches("sub/pyproject.toml", "pyproject.toml")


def test_glob_pattern_match(cl):
    assert cl._matches("docs/architecture.md", "docs/architecture.md")
    assert not cl._matches("docs/secret_strategy.md", "docs/architecture.md")


def test_docs_are_enumerated_not_wildcarded(cl):
    """The dev-doc review gate depends on docs/ NOT being a blanket
    glob. A brand-new docs file must NOT be auto-covered."""
    patterns = cl._load_allowlist()
    # No entry should wildcard the whole docs tree.
    assert "docs/**" not in patterns
    # A hypothetical new doc is therefore uncovered.
    new_doc = "docs/some_brand_new_internal_note.md"
    assert not any(cl._matches(new_doc, p) for p in patterns), (
        "a new docs/*.md must be uncovered until explicitly allowlisted "
        "(dev-doc review gate)"
    )


# ── real-repo invariant ─────────────────────────────────────────────


def test_every_tracked_file_is_covered(cl):
    """The whole point: the current tracked tree must be fully covered.
    If this fails, either a sensitive file leaked into tracking or a
    legit file needs a .publishallow entry."""
    patterns = cl._load_allowlist()
    tracked = subprocess.check_output(
        ["git", "ls-files"], text=True, encoding="utf-8", cwd=_REPO_ROOT,
    ).splitlines()
    tracked = [t.replace("\\", "/") for t in tracked if t]
    uncovered = [t for t in tracked if not any(cl._matches(t, p) for p in patterns)]
    assert not uncovered, f"uncovered tracked files: {uncovered[:20]}"


def test_experiments_not_tracked(cl):
    """experiments/ was removed from tracking in v3.31 (internal R&D)."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "experiments/"], text=True, encoding="utf-8",
        cwd=_REPO_ROOT,
    ).splitlines()
    assert not [t for t in tracked if t], "experiments/ must not be tracked"


def test_allowlist_itself_is_allowed(cl):
    """.publishallow must allow itself (it's tracked + public)."""
    patterns = cl._load_allowlist()
    assert any(cl._matches(".publishallow", p) for p in patterns)
