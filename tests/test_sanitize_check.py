"""Tests for scripts/release_sanitize_check.py (v3.31 P1-2).

Loads the script module by path (scripts/ is not an installed package)
and exercises the pattern set + per-file scanning.
"""

from __future__ import annotations

import importlib.util
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "release_sanitize_check.py"
)
_ARTIFACT_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_release_artifact_private_terms.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_sanitize_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_artifact_module():
    spec = importlib.util.spec_from_file_location("_artifact_private_scan", _ARTIFACT_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sc():
    return _load_module()


# ── live-credential scan: real keys flagged even in fixtures (2026-06-14) ──


def test_live_credential_scan_flags_unlisted_key(sc, tmp_path):
    # Build the token at runtime so THIS test's own source carries no literal
    # ``sk-<token>`` for the scanner to (correctly) flag.
    fake = "sk-" + "9f3a" + "c2b8" + "d1e4" + "0a7c" + "9562" + "fb38" + "11aa"
    f = tmp_path / "test_fixture_like.py"
    f.write_text(f'KEY = "{fake}"\n', encoding="utf-8")
    hits = sc._scan_live_credentials(f)
    assert any(sev == "high" and label == "live credential"
               for label, sev, _lineno, _preview in hits)
    # the raw secret is never echoed back — only a short prefix preview
    assert all(fake not in preview for _l, _s, _ln, preview in hits)


def test_live_credential_scan_allows_known_dummy(sc, tmp_path):
    f = tmp_path / "test_fixture_like.py"
    f.write_text('KEY = "sk-abcdef1234567890abcdef"\n', encoding="utf-8")
    assert sc._scan_live_credentials(f) == []


def test_live_credential_scan_catches_sk_proj(sc, tmp_path):
    # the built-in OpenAI pattern (sk-[A-Za-z0-9]{20,}) missed sk-proj-…;
    # the live scan must catch it.
    proj = "sk-proj-" + "Zx9" + "Qw8" + "Er7" + "Ty6" + "Ui5" + "Op4" + "As3"
    f = tmp_path / "test_fixture_like.py"
    f.write_text(f'k = "{proj}"\n', encoding="utf-8")
    assert any(sev == "high" for _l, sev, _ln, _p in sc._scan_live_credentials(f))


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
        r"high:PRIVATE_CANARY_DO_NOT_RELEASE" + "\n"
        r"\bINTERNAL_MODEL_REVIEW\b" + "\n"
        r"PRIVATE_REVIEW_GATE" + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loaded = sc._load_internal_patterns_file()
    assert len(loaded) == 3
    assert loaded[0][2] == "high"
    assert all(severity == "warn" for _, _, severity in loaded[1:])
    # And they actually match the project-specific strings.
    doc = tmp_path / "d.md"
    doc.write_text(
        "PRIVATE_CANARY_DO_NOT_RELEASE judged by INTERNAL_MODEL_REVIEW after PRIVATE_REVIEW_GATE\n",
        encoding="utf-8",
    )
    hits = sc._scan_file(doc, [], loaded)
    assert any(label == "local#2" and sev == "high" for label, sev, _, _ in hits)
    assert any(label == "local#3" and sev == "warn" for label, sev, _, _ in hits)
    assert any(label == "local#4" and sev == "warn" for label, sev, _, _ in hits)


def test_external_patterns_file_absent_returns_empty(sc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .sanitizeignore here
    assert sc._load_internal_patterns_file() == []


def test_release_artifact_private_scan_checks_built_packages(tmp_path, monkeypatch):
    artifact_scan = _load_artifact_module()
    root = tmp_path / "repo"
    dist = root / "dist"
    dist.mkdir(parents=True)
    (root / ".sanitizeignore").write_text(
        "high:PRIVATE_PLAYBOOK_CANARY\n",
        encoding="utf-8",
    )

    whl = dist / "pkg-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("pkg-1.0.0.dist-info/METADATA", "PRIVATE_PLAYBOOK_CANARY\n")

    sdist = dist / "pkg-1.0.0.tar.gz"
    payload = tmp_path / "PKG-INFO"
    payload.write_text("clean metadata\n", encoding="utf-8")
    with tarfile.open(sdist, "w:gz") as tf:
        tf.add(payload, arcname="pkg-1.0.0/PKG-INFO")

    monkeypatch.chdir(root)
    hits = artifact_scan.scan_artifacts(dist, root)

    assert any(
        label == ".sanitizeignore#1" and severity == "high" and "METADATA" in rel
        for label, severity, _, rel, _ in hits
    )


def test_main_no_custom_terms_message_is_plain_ascii(sc, tmp_path, monkeypatch, capsys):
    """User-visible sanitize output should avoid mojibake-prone punctuation."""
    import sys

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sc, "_git_tracked_files", lambda: [])
    monkeypatch.setattr(sc, "_load_custom_terms", lambda: [])
    monkeypatch.setattr(sys, "argv", ["release_sanitize_check.py"])

    assert sc.main() == 0

    out = capsys.readouterr().out
    assert " - only built-in patterns" in out
    assert "鈥" not in out


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


# ── v4.1.x: Windows-path regex covers single- AND double-backslash ──────


def test_windows_path_matches_single_backslash(sc, tmp_path):
    """The v4.1.0 leak: a real path in a markdown/plain-text doc uses one
    backslash (``C:\\Users\\name``). The old ``\\\\`` (exactly two) regex
    missed it. The fixed regex must flag the single-backslash form."""
    f = tmp_path / "doc.md"
    f.write_text(r"old artifacts were under C:\Users\someone\proj" + "\n",
                 encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS)
    assert any(label == "Windows path" for label, *_ in hits), \
        "single-backslash Windows path was not caught"


def test_windows_path_still_matches_double_backslash(sc, tmp_path):
    """Source-escaped form (``C:\\\\Users\\\\name`` on disk) stays caught."""
    f = tmp_path / "code.py"
    f.write_text('p = "C:\\\\Users\\\\someone\\\\proj"\n', encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS)
    assert any(label == "Windows path" for label, *_ in hits)


def test_windows_path_marker_without_username_not_flagged(sc, tmp_path):
    """``C:\\Users`` with no trailing ``\\name`` (a redaction marker
    constant) is not a leaked path and must not trip the pattern."""
    f = tmp_path / "marker.py"
    f.write_text('MARKER = r"C:\\Users"\n', encoding="utf-8")
    hits = sc._scan_file(f, [], sc._BUILT_IN_PATTERNS)
    assert not any(label == "Windows path" for label, *_ in hits)


# ── v4.1.x: fixture-bearing files scanned only for real private terms ───


def test_is_fixture_predicate(sc):
    assert sc._is_fixture("tests/test_x.py")
    assert sc._is_fixture("scripts/check_generated_export_redaction.py")
    assert not sc._is_fixture("src/piia_engram/core.py")
    assert not sc._is_fixture("docs/benchmarks/recall-eval-v1.md")


def test_tests_dir_demoted_from_full_skip(sc):
    """``tests/`` must no longer be in the full-skip list (that hid a real
    leaked path); it is now fixture-exempt instead."""
    assert not any("tests/" == g for g in sc._SKIP_GLOBS)
    assert "tests/" in sc._FIXTURE_GLOBS


def test_lookahead_term_excludes_reverse_assertion(sc):
    """A ``.sanitizeignore`` lookahead term such as ``USER(?!25)`` must
    still catch a real path leak but NOT a deliberate reverse-assertion
    fixture like ``USER25`` (the shape used in
    test_telemetry_endpoint_decouple.py). Uses a neutral placeholder so
    this test file carries no real private identifier."""
    pat = re.compile(r"sampleuser(?!25)")
    assert pat.search(r"C:\Users\sampleuser\secret"), "real leak must match"
    assert not pat.search("sampleuser25"), "reverse-assertion must stay clean"


def test_fixture_file_scanned_only_for_local_terms(sc, tmp_path, monkeypatch, capsys):
    """End-to-end: a file under tests/ may legitimately hold a fake path
    fixture (must be ignored) while a real private term from
    ``.sanitizeignore`` in the same file must still be caught HIGH."""
    import subprocess
    import sys

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    # .sanitizeignore is loaded from CWD (not via git), so do NOT add it.
    (tmp_path / ".sanitizeignore").write_text("high:CANARYLEAK\n", encoding="utf-8")

    testsdir = tmp_path / "tests"
    testsdir.mkdir()
    (testsdir / "test_fix.py").write_text(
        'WIN = "C:\\\\Users\\\\victim\\\\x"  # fake fixture, must be ignored\n'
        'TERM = "CANARYLEAK"  # real private term, must be caught\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "tests/test_fix.py"], cwd=tmp_path, check=True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sc, "_load_custom_terms", lambda: [])
    monkeypatch.setattr(sys, "argv",
                        ["release_sanitize_check.py", "--strict", "--internal"])

    rc = sc.main()
    out = capsys.readouterr().out

    # The fake fixture path must NOT be reported (fixture-exempt).
    assert "Windows path" not in out
    # The real private term MUST be reported HIGH and block release.
    assert "local#1" in out and "tests/test_fix.py" in out
    assert rc == 1


def test_fixture_file_skipped_without_internal(sc, tmp_path, monkeypatch, capsys):
    """Without --internal there are no real-term patterns loaded, so a
    fixture file is skipped entirely (no built-in fixture false positives,
    same net effect as the old full-skip)."""
    import subprocess
    import sys

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    testsdir = tmp_path / "tests"
    testsdir.mkdir()
    (testsdir / "test_fix.py").write_text(
        'WIN = "C:\\\\Users\\\\victim\\\\x"\n'
        'KEY = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "tests/test_fix.py"], cwd=tmp_path, check=True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sc, "_load_custom_terms", lambda: [])
    monkeypatch.setattr(sys, "argv", ["release_sanitize_check.py", "--strict"])

    rc = sc.main()
    out = capsys.readouterr().out

    assert "Windows path" not in out
    assert "GitHub token" not in out
    assert rc == 0


def test_fixture_file_private_workspace_path_still_scanned(sc, tmp_path, monkeypatch, capsys):
    """Fixture exemption must not hide a real maintainer workspace path."""
    import subprocess
    import sys

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    testsdir = tmp_path / "tests"
    testsdir.mkdir()
    workspace_name = "Acme Internal " + "Workspace Name"
    (testsdir / "test_fix.py").write_text(
        f'PROJECT = "E:/{workspace_name}/engram"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "tests/test_fix.py"], cwd=tmp_path, check=True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sc, "_load_custom_terms", lambda: [])
    monkeypatch.setattr(sys, "argv", ["release_sanitize_check.py", "--strict"])

    rc = sc.main()
    out = capsys.readouterr().out

    assert "private local path" in out
    assert "tests/test_fix.py" in out
    assert rc == 1


def test_private_path_scan_flags_drive_temp_tool_path(sc, tmp_path):
    """Non-Users Windows paths can still disclose maintainer machine layout."""
    f = tmp_path / "release.py"
    drive = "E:"
    temp_dir = "Temp"
    f.write_text(f'PUBLISHER = "{drive}\\\\{temp_dir}\\\\mcp-publisher.exe"\n',
                 encoding="utf-8")

    hits = sc._scan_private_local_paths(f)

    assert any(label == "private local path" and sev == "warn" for label, sev, *_ in hits)
