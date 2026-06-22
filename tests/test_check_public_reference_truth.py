"""Tests for scripts/check_public_reference_truth.py.

The guard makes the project's *self-verification* material honest: every local
link and ``scripts/*.py`` command referenced from a PUBLIC surface must point at
a file that actually exists. (The repo shipped a public-facts.json that pointed
at a runbook which had been deleted — exactly the kind of "trust evidence that
references missing trust evidence" this guard catches.)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_public_reference_truth.py"
_spec = importlib.util.spec_from_file_location("check_public_reference_truth", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _doc(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Link classification
# ---------------------------------------------------------------------------


class TestLinkClassification:
    def test_urls_and_anchors_are_not_local(self):
        for t in ["https://x.com", "http://x", "mailto:a@b.c", "#section", "tel:+1"]:
            assert guard.is_local_link(t) is False

    def test_relative_paths_are_local(self):
        for t in ["docs/trust.md", "../LICENSE", "assets/x.png", "CONTRIBUTING.md"]:
            assert guard.is_local_link(t) is True

    def test_target_strips_anchor_and_title(self):
        assert guard.link_target_path('docs/x.md#sec') == "docs/x.md"
        assert guard.link_target_path('docs/x.md "a title"') == "docs/x.md"


# ---------------------------------------------------------------------------
# Markdown surface checks
# ---------------------------------------------------------------------------


class TestMarkdown:
    def test_good_link_passes(self, tmp_path: Path):
        _doc(tmp_path, "docs/trust.md", "# trust")
        f = _doc(tmp_path, "README.md", "See [trust](docs/trust.md).")
        assert guard.check_markdown(f, tmp_path) == []

    def test_dead_link_detected(self, tmp_path: Path):
        f = _doc(tmp_path, "README.md", "See [gone](docs/missing.md).")
        errors = guard.check_markdown(f, tmp_path)
        assert any("docs/missing.md" in e for e in errors)

    def test_url_and_anchor_links_skipped(self, tmp_path: Path):
        f = _doc(tmp_path, "README.md", "[site](https://x.com) and [top](#top)")
        assert guard.check_markdown(f, tmp_path) == []

    def test_anchor_on_existing_file_resolves(self, tmp_path: Path):
        _doc(tmp_path, "docs/trust.md", "# trust\n## sec")
        f = _doc(tmp_path, "README.md", "[s](docs/trust.md#sec)")
        assert guard.check_markdown(f, tmp_path) == []

    def test_relative_link_resolves_from_file_dir(self, tmp_path: Path):
        _doc(tmp_path, "docs/runbooks/a.md", "# a")
        f = _doc(tmp_path, "docs/telemetry.md", "see [a](runbooks/a.md)")
        assert guard.check_markdown(f, tmp_path) == []

    def test_missing_script_command_detected(self, tmp_path: Path):
        f = _doc(tmp_path, "README.md", "Run `python scripts/nope.py --x`.")
        errors = guard.check_markdown(f, tmp_path)
        assert any("scripts/nope.py" in e for e in errors)

    def test_present_script_command_passes(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "real.py").write_text("x=1\n", encoding="utf-8")
        f = _doc(tmp_path, "README.md", "Run `python scripts/real.py`.")
        assert guard.check_markdown(f, tmp_path) == []


# ---------------------------------------------------------------------------
# JSON manifest (public-facts.json references)
# ---------------------------------------------------------------------------


class TestJsonManifest:
    def test_missing_referenced_path_detected(self, tmp_path: Path):
        f = _doc(
            tmp_path,
            "docs/public-facts.json",
            json.dumps({"_about": "See docs/runbooks/public-truth-sync.md for details."}),
        )
        errors = guard.check_json_manifest(f, tmp_path)
        assert any("public-truth-sync.md" in e for e in errors)

    def test_present_referenced_path_passes(self, tmp_path: Path):
        _doc(tmp_path, "docs/runbooks/public-truth-sync.md", "# runbook")
        f = _doc(
            tmp_path,
            "docs/public-facts.json",
            json.dumps({"_about": "See docs/runbooks/public-truth-sync.md."}),
        )
        assert guard.check_json_manifest(f, tmp_path) == []


# ---------------------------------------------------------------------------
# Surface discovery + scan
# ---------------------------------------------------------------------------


class TestScan:
    def test_internal_docs_are_excluded(self, tmp_path: Path):
        # a dead link inside docs/internal/ must NOT be reported
        _doc(tmp_path, "docs/internal/notes.md", "[x](docs/does-not-exist.md)")
        _doc(tmp_path, "README.md", "ok")
        errors = guard.scan(tmp_path)
        assert errors == []

    def test_scan_reports_dead_public_link(self, tmp_path: Path):
        _doc(tmp_path, "README.md", "[x](docs/missing.md)")
        errors = guard.scan(tmp_path)
        assert any("docs/missing.md" in e for e in errors)

    def test_changelog_is_excluded(self, tmp_path: Path):
        # historical changelog entries may name since-moved files
        _doc(tmp_path, "CHANGELOG.md", "Added `docs/old/gone.md`")
        _doc(tmp_path, "README.md", "ok")
        assert guard.scan(tmp_path) == []

    def test_gitignored_target_is_dead_even_if_on_disk(self, tmp_path: Path):
        # A referenced file that exists locally but is gitignored is NOT published
        # — so it must be flagged (it would 404 for anyone who clones the repo).
        import subprocess

        def g(*a):
            subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        _doc(tmp_path, "docs/secret-runbook.md", "# internal only")
        _doc(tmp_path, ".gitignore", "docs/secret-runbook.md\n")
        _doc(tmp_path, "README.md", "see [it](docs/secret-runbook.md)")
        g("add", "-A")
        g("commit", "-qm", "init")
        errors = guard.scan(tmp_path)
        assert any("secret-runbook.md" in e for e in errors)

    def test_case_mismatch_link_is_dead_in_git_mode(self, tmp_path: Path):
        # git is case-sensitive; a wrong-case link is a 404 on Linux/clones even
        # though Windows resolve() would canonicalize it. Must be flagged.
        import subprocess

        def g(*a):
            subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        _doc(tmp_path, "docs/Trust.md", "# Trust")
        _doc(tmp_path, "README.md", "see [t](docs/trust.md)")  # wrong case
        g("add", "-A")
        g("commit", "-qm", "init")
        errors = guard.scan(tmp_path)
        assert any("docs/trust.md" in e for e in errors)

    def test_main_exit_codes(self, tmp_path: Path):
        _doc(tmp_path, "README.md", "ok, no links")
        assert guard.main(["--root", str(tmp_path)]) == 0
        _doc(tmp_path, "README.md", "[bad](docs/nope.md)")
        assert guard.main(["--root", str(tmp_path)]) == 1
