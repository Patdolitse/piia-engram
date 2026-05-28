"""Tests for scripts/release_sanitize_check.py (v3.31 P1-2).

Loads the script module by path (scripts/ is not an installed package)
and exercises the pattern set + per-file scanning.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "release_sanitize_check.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_sanitize_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sc():
    return _load_module()


# ── built-in secret patterns still fire ────────────────────────────────


def test_high_patterns_detect_github_token(sc, tmp_path):
    f = tmp_path / "leak.txt"
    f.write_text('token = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"\n', encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS)
    labels = {h[0] for h in hits}
    assert "GitHub token" in labels
    assert any(sev == "high" for _, sev, _, _ in hits)


def test_high_patterns_detect_pem(sc, tmp_path):
    f = tmp_path / "key.pem"
    f.write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS)
    assert any(label == "PEM private" and sev == "high" for label, sev, _, _ in hits)


# ── v3.31 P1-2 internal-disclosure patterns ────────────────────────────


def test_internal_patterns_not_in_builtin_by_default(sc):
    """Internal patterns are a separate opt-in group."""
    builtin_labels = {label for label, _, _ in sc._BUILT_IN_PATTERNS}
    internal_labels = {label for label, _, _ in sc._INTERNAL_DISCLOSURE_PATTERNS}
    assert not (builtin_labels & internal_labels), "groups must be disjoint"


@pytest.mark.parametrize("text,expect_label", [
    ("pre-release found 8 HIGH / 15 MEDIUM blockers", "review code count"),
    ("our industry-first time-based snapshot", "industry-first claim"),
    ("verified in prior art flush.py:17", "prior-art line ref"),
    ("issue_id=4277 in the internal tracker", "internal issue id"),
])
def test_generic_internal_patterns_match(sc, tmp_path, text, expect_label):
    """The GENERIC OPSEC patterns stay inlined in the public script."""
    f = tmp_path / "doc.md"
    f.write_text(text + "\n", encoding="utf-8")
    patterns = sc._BUILT_IN_PATTERNS + sc._INTERNAL_DISCLOSURE_PATTERNS
    hits = sc._scan_file(f, [], patterns)
    labels = {h[0] for h in hits}
    assert expect_label in labels, f"{expect_label!r} not in {labels}"


def test_project_specifics_not_inlined_in_public_script(sc):
    """v3.31: project-identifying patterns (review-process names, eval
    model codenames) must NOT be hardcoded in the public script — they
    live in the gitignored .sanitizeignore so the script doesn't
    broadcast our specific sensitivities."""
    inlined = "\n".join(p.pattern for _, p, _ in sc._INTERNAL_DISCLOSURE_PATTERNS)
    assert "DeepSeek" not in inlined
    assert "way review" not in inlined
    assert "守门" not in inlined


def test_external_patterns_file_loader(sc, tmp_path, monkeypatch):
    """_load_internal_patterns_file reads regexes from the gitignored
    file when present (run from a dir that contains it)."""
    patterns_file = tmp_path / ".sanitizeignore"
    patterns_file.write_text(
        "# comment\n"
        r"\bDeepSeek[\s-]*V\d+\b" + "\n"
        r"[三四五]方(?:审查|评审)" + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loaded = sc._load_internal_patterns_file()
    assert len(loaded) == 2
    # And they actually match the project-specific strings.
    doc = tmp_path / "d.md"
    doc.write_text("judged by DeepSeek V4 Pro after 五方审查\n", encoding="utf-8")
    hits = sc._scan_file(doc, [], loaded)
    assert len(hits) >= 2


def test_external_patterns_file_absent_returns_empty(sc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .sanitizeignore here
    assert sc._load_internal_patterns_file() == []


def test_internal_patterns_are_warn_not_high(sc):
    """Internal disclosure is warn-level — informative, not a hard block
    unless --strict."""
    for _, _, severity in sc._INTERNAL_DISCLOSURE_PATTERNS:
        assert severity == "warn"


def test_ordinary_code_does_not_false_positive(sc, tmp_path):
    """Plain code with variables like R3 / H1 in unrelated context should
    not trip the (deliberately specific) internal patterns."""
    f = tmp_path / "code.py"
    f.write_text(
        "register_value = H1 + R3  # arithmetic, not review codes\n"
        "model = 'gpt-4o'  # not a deepseek codename\n",
        encoding="utf-8",
    )
    patterns = sc._BUILT_IN_PATTERNS + sc._INTERNAL_DISCLOSURE_PATTERNS
    hits = sc._scan_file(f, [], patterns)
    internal_labels = {label for label, _, _ in sc._INTERNAL_DISCLOSURE_PATTERNS}
    tripped = {h[0] for h in hits} & internal_labels
    assert not tripped, f"false positives: {tripped}"


# ── --staged plumbing exists ────────────────────────────────────────────


def test_staged_files_helper_exists(sc):
    assert hasattr(sc, "_git_staged_files")
    assert callable(sc._git_staged_files)


# ── v3.33.2: --staged scans the index blob, not the working tree ─────


def test_staged_scan_reads_index_blob_not_worktree(sc, tmp_path, monkeypatch):
    """A secret git-add-ed then removed from the work tree (without
    re-staging) must STILL be caught — the staged blob is what commits."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    leak = tmp_path / "leak.txt"
    leak.write_text('token = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"\n', encoding="utf-8")
    subprocess.run(["git", "add", "leak.txt"], cwd=tmp_path, check=True)
    # work tree cleaned but NOT re-added → index still holds the secret
    leak.write_text("clean now\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    blob = sc._read_staged_blob("leak.txt")
    assert blob is not None and "ghp_" in blob
    staged_hits = sc._scan_file(Path("leak.txt"), [], sc._BUILT_IN_PATTERNS, text=blob)
    assert any(sev == "high" for _, sev, _, _ in staged_hits), "staged secret missed"

    # proves the bug class: scanning the working tree would MISS it
    wt_hits = sc._scan_file(tmp_path / "leak.txt", [], sc._BUILT_IN_PATTERNS)
    assert not any(sev == "high" for _, sev, _, _ in wt_hits)


def test_scan_file_accepts_text_override(sc, tmp_path):
    """_scan_file scans provided text even if the file on disk differs."""
    f = tmp_path / "x.txt"
    f.write_text("totally clean\n", encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS,
                         text='ghp_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB5678')
    assert any(label == "GitHub token" for label, *_ in hits)


# ── v3.32 P1: multi-line docstring scanning ─────────────────────────────


def test_multiline_catches_phrase_wrapped_in_docstring(sc, tmp_path):
    """A line-by-line scan misses an internal phrase split across a line
    break inside a docstring; the multiline pass catches it."""
    f = tmp_path / "mod.py"
    f.write_text(
        '"""Module.\n\nThis is our industry-\nfirst snapshot approach.\n"""\n',
        encoding="utf-8",
    )
    # per-line scanner does NOT see the wrapped phrase
    line_hits = sc._scan_file(f, [], sc._INTERNAL_DISCLOSURE_PATTERNS)
    assert not any("industry" in label for label, *_ in line_hits)
    # multiline scanner DOES
    ml_hits = sc._scan_file_multiline(f, sc._INTERNAL_DISCLOSURE_PATTERNS)
    assert any("industry-first" in label for label, *_ in ml_hits)
    assert all("(multiline)" in label for label, *_ in ml_hits)


def test_multiline_skips_single_line_hits(sc, tmp_path):
    """Single-line matches stay the responsibility of _scan_file — the
    multiline pass must not double-report them."""
    f = tmp_path / "mod.py"
    f.write_text('x = "our industry-first thing"\n', encoding="utf-8")
    assert sc._scan_file_multiline(f, sc._INTERNAL_DISCLOSURE_PATTERNS) == []


def test_multiline_only_scans_text_extensions(sc, tmp_path):
    """Non-prose extensions are skipped to keep the scan cheap/targeted."""
    f = tmp_path / "data.json"
    f.write_text("industry-\nfirst\n", encoding="utf-8")
    assert f.suffix.lower() not in sc._MULTILINE_EXTS
    assert sc._scan_file_multiline(f, sc._INTERNAL_DISCLOSURE_PATTERNS) == []


def test_multiline_reports_correct_start_line(sc, tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "line1\nline2\nx = 'industry-\nfirst'\n",
        encoding="utf-8",
    )
    ml_hits = sc._scan_file_multiline(f, sc._INTERNAL_DISCLOSURE_PATTERNS)
    assert ml_hits, "expected a multiline hit"
    # match starts on line 3 ("x = 'industry-")
    assert ml_hits[0][2] == 3
