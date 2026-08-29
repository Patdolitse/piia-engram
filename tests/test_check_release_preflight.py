"""Tests for scripts/check_release_preflight.py (release-candidate invariant).

The preflight is the structural pre-tag gate that makes the v4.10.0 failure
mode (tag/release created without a matching, committed release-evidence file)
impossible. It is deliberately strict about binding evidence to the **HEAD
tree** that will be tagged — not just the working copy or the index.

Tests build real throwaway git repos under tmp_path so the git-tree checks
(`git cat-file -e HEAD:...`, `git ls-files`, `git tag`) exercise real plumbing.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

# Load the script as a module by path (scripts/ is not a package).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_release_preflight.py"
_spec = importlib.util.spec_from_file_location("check_release_preflight", _SCRIPT)
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


# ---------------------------------------------------------------------------
# Repo fixture helpers
# ---------------------------------------------------------------------------

_REQUIRED_EVIDENCE = """# Release evidence - v{v}

- self-review: passed
- implementation-review: passed
- acceptance-review: passed
- tests: passed
- sanitize: passed
- publish-allowlist: passed
- package-build: passed
- artifact-private-scan: passed
- twine-check: passed
- eval-gate: n/a
- negative-control: n/a
- field-assertion-audit: n/a
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_version_files(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "piia-engram"\nversion = "{version}"\n', encoding="utf-8"
    )
    (repo / "src" / "piia_engram").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "piia_engram" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (repo / ".mcp").mkdir(exist_ok=True)
    server = {
        "version": version,
        "packages": [
            {
                "version": version,
                "runtimeArguments": [
                    {"name": "--from", "value": f"piia-engram=={version}"}
                ],
            }
        ],
    }
    (repo / ".mcp" / "server.json").write_text(
        json.dumps(server, indent=2) + "\n", encoding="utf-8"
    )
    (repo / ".claude-plugin").mkdir(exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": version}, indent=2) + "\n", encoding="utf-8"
    )
    (repo / "glama.yaml").write_text(f"version: {version}\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "public-facts.json").write_text(
        json.dumps({"local_dev_version": version}, indent=2) + "\n", encoding="utf-8"
    )
    (repo / "README.md").write_text(
        f"| Version frame | **v{version}** (verified) |\n", encoding="utf-8"
    )
    (repo / "README.zh-CN.md").write_text(
        f"| 版本口径 | **v{version}**（已核验）|\n", encoding="utf-8"
    )


def _copy_release_gate(repo: Path) -> None:
    """Vendor the real check_release_gate.py so the preflight can call it."""
    (repo / "scripts").mkdir(exist_ok=True)
    real = _SCRIPT.parent / "check_release_gate.py"
    (repo / "scripts" / "check_release_gate.py").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8"
    )


def make_repo(tmp_path: Path, version: str = "4.12.0", *, evidence: bool = True,
              committed: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write_version_files(repo, version)
    _copy_release_gate(repo)
    if evidence:
        (repo / "release-evidence").mkdir(exist_ok=True)
        (repo / "release-evidence" / f"v{version}.md").write_text(
            _REQUIRED_EVIDENCE.format(v=version), encoding="utf-8"
        )
        (repo / ".publishallow").write_text(
            f"release-evidence/v{version}.md\n", encoding="utf-8"
        )
    else:
        (repo / ".publishallow").write_text("# nothing\n", encoding="utf-8")
    if committed:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
    return repo


# ---------------------------------------------------------------------------
# Version consistency (default mode — always-on invariant)
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    def test_passes_when_all_surfaces_aligned(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        assert preflight.check_version_consistency(repo) == []

    def test_detects_init_mismatch(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        (repo / "src" / "piia_engram" / "__init__.py").write_text(
            '__version__ = "4.11.0"\n', encoding="utf-8"
        )
        errors = preflight.check_version_consistency(repo)
        assert any("__init__" in e for e in errors)

    def test_detects_server_json_from_mismatch(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        data = json.loads((repo / ".mcp" / "server.json").read_text(encoding="utf-8"))
        data["packages"][0]["runtimeArguments"][0]["value"] = "piia-engram==4.11.0"
        (repo / ".mcp" / "server.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
        errors = preflight.check_version_consistency(repo)
        assert any("server.json" in e for e in errors)

    def test_default_mode_passes_without_tag(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, tag=None)
        assert result.ok, result.errors

    def test_missing_surface_file_fails_closed(self, tmp_path: Path):
        # A surface that disappears must FAIL (not silently pass) — otherwise a
        # half-bumped release could slip through.
        repo = make_repo(tmp_path, "4.12.0")
        (repo / "glama.yaml").unlink()
        errors = preflight.check_version_consistency(repo)
        assert any("glama.yaml" in e and "not found" in e for e in errors)


# ---------------------------------------------------------------------------
# Tag-mode structural gate
# ---------------------------------------------------------------------------


class TestTagMode:
    def test_passes_for_well_formed_release(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, tag="v4.12.0")
        assert result.ok, result.errors

    def test_tag_must_match_pyproject_version(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, tag="v9.9.9")
        assert not result.ok
        assert any("tag" in e.lower() for e in result.errors)

    def test_rejects_non_final_semver(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0rc1", evidence=False)
        result = preflight.preflight(repo, tag="v4.12.0rc1")
        assert not result.ok
        assert any("semver" in e.lower() for e in result.errors)

    def test_evidence_must_be_in_head_tree(self, tmp_path: Path):
        # Evidence written + allowlisted on disk, but NOT committed → not in HEAD.
        repo = make_repo(tmp_path, "4.12.0", evidence=False)
        (repo / "release-evidence").mkdir(exist_ok=True)
        (repo / "release-evidence" / "v4.12.0.md").write_text(
            _REQUIRED_EVIDENCE.format(v="4.12.0"), encoding="utf-8"
        )
        (repo / ".publishallow").write_text(
            "release-evidence/v4.12.0.md\n", encoding="utf-8"
        )
        # left uncommitted on purpose
        result = preflight.preflight(repo, tag="v4.12.0")
        assert not result.ok
        assert any("HEAD" in e or "head" in e for e in result.errors)

    def test_evidence_committed_passes_head_check(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        assert preflight.evidence_in_head(repo, "4.12.0") is True

    def test_evidence_missing_from_allowlist_fails(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        (repo / ".publishallow").write_text("# removed\n", encoding="utf-8")
        _git(repo, "commit", "-aqm", "drop allowlist")
        result = preflight.preflight(repo, tag="v4.12.0")
        assert not result.ok
        assert any("publishallow" in e.lower() or "allowlist" in e.lower()
                   for e in result.errors)

    def test_incomplete_evidence_markers_fail(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        (repo / "release-evidence" / "v4.12.0.md").write_text(
            "# Release evidence - v4.12.0\n\n- self-review: pending\n", encoding="utf-8"
        )
        _git(repo, "commit", "-aqm", "break evidence")
        result = preflight.preflight(repo, tag="v4.12.0")
        assert not result.ok
        assert any("gate" in e.lower() or "evidence" in e.lower()
                   for e in result.errors)

    def test_existing_local_tag_fails(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        _git(repo, "tag", "v4.12.0")
        result = preflight.preflight(repo, tag="v4.12.0")
        assert not result.ok
        assert any("tag" in e.lower() and "exist" in e.lower() for e in result.errors)

    def test_dirty_worktree_fails(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        (repo / "pyproject.toml").write_text(
            (repo / "pyproject.toml").read_text(encoding="utf-8") + "\n# dirty\n",
            encoding="utf-8",
        )
        result = preflight.preflight(repo, tag="v4.12.0")
        assert not result.ok
        assert any("clean" in e.lower() or "dirty" in e.lower()
                   for e in result.errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestSinceMode:
    """--since <ref>: a commit that bumps the version must carry its evidence.

    This is the CI-enforced structural guarantee — by the time main HEAD has
    version V, release-evidence/vV.md is in that same commit, so tagging main
    HEAD can never miss it (the v4.10.0 failure mode).
    """

    def _bump_to(self, repo: Path, version: str, *, with_evidence: bool) -> None:
        _write_version_files(repo, version)
        if with_evidence:
            (repo / "release-evidence" / f"v{version}.md").write_text(
                _REQUIRED_EVIDENCE.format(v=version), encoding="utf-8"
            )
            allow = (repo / ".publishallow").read_text(encoding="utf-8")
            (repo / ".publishallow").write_text(
                allow + f"release-evidence/v{version}.md\n", encoding="utf-8"
            )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"bump {version}")

    def test_no_version_change_passes_without_evidence(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        # a non-version commit on top
        (repo / "README.md").write_text(
            (repo / "README.md").read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8"
        )
        _git(repo, "commit", "-aqm", "docs tweak")
        result = preflight.preflight(repo, since="HEAD~1")
        assert result.ok, result.errors

    def test_version_bump_without_evidence_fails(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        self._bump_to(repo, "4.13.0", with_evidence=False)
        result = preflight.preflight(repo, since="HEAD~1")
        assert not result.ok
        assert any("4.13.0" in e and ("evidence" in e.lower() or "HEAD" in e)
                   for e in result.errors)

    def test_version_bump_with_evidence_passes(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        self._bump_to(repo, "4.13.0", with_evidence=True)
        result = preflight.preflight(repo, since="HEAD~1")
        assert result.ok, result.errors

    def test_unknown_base_ref_is_lenient(self, tmp_path: Path):
        # Cannot read base (shallow clone / first release) -> do not block by default.
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, since="does-not-exist")
        assert result.ok, result.errors

    def test_empty_since_is_rejected(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, since="")
        assert not result.ok
        assert any("empty" in e.lower() for e in result.errors)

    def test_unreadable_base_with_required_fails_closed(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, since="deadbeefdeadbeef", base_required=True)
        assert not result.ok
        assert any("base" in e.lower() for e in result.errors)

    def test_all_zeros_base_falls_back_to_main_and_catches_bump(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")  # commit A
        # v4.19.1: the all-zeros path runs the REAL bounded fallback, which
        # needs an actual origin to fetch from (a bare remote; local commits
        # after the push give distance 1, inside the default bound)
        _add_origin_remote(repo, tmp_path)
        self._bump_to(repo, "4.13.0", with_evidence=False)  # commit B (HEAD)
        result = preflight.preflight(repo, since="0" * 40, base_required=True)
        assert not result.ok  # fallback sees the bump vs origin/main, no evidence
        assert any("4.13.0" in e for e in result.errors)

    def test_all_zeros_base_fallback_passes_with_evidence(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        _add_origin_remote(repo, tmp_path)
        self._bump_to(repo, "4.13.0", with_evidence=True)
        result = preflight.preflight(repo, since="0" * 40, base_required=True)
        assert result.ok, result.errors

    def test_version_at_ref_reads_old_version(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        self._bump_to(repo, "4.13.0", with_evidence=True)
        assert preflight.version_at_ref(repo, "HEAD~1") == "4.12.0"
        assert preflight.version_at_ref(repo, "HEAD") == "4.13.0"

    def test_version_at_ref_handles_non_ascii_pyproject(self, tmp_path: Path):
        # Regression: `git show` output must be decoded as UTF-8, not the locale
        # codec (GBK on a zh Windows box choked on the em-dash before the fix).
        repo = make_repo(tmp_path, "4.12.0")
        pp = repo / "pyproject.toml"
        pp.write_text(
            pp.read_text(encoding="utf-8") + 'description = "local — memory"\n',
            encoding="utf-8",
        )
        _git(repo, "commit", "-aqm", "non-ascii description")
        assert preflight.version_at_ref(repo, "HEAD") == "4.12.0"


class TestCli:
    def test_main_exit0_on_clean_repo_default_mode(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        assert preflight.main(["--root", str(repo)]) == 0

    def test_main_exit1_on_version_drift(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        (repo / "glama.yaml").write_text("version: 4.11.0\n", encoding="utf-8")
        assert preflight.main(["--root", str(repo)]) == 1

    def test_main_exit1_on_bad_tag(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        assert preflight.main(["--root", str(repo), "--tag", "v9.9.9"]) == 1

    def test_main_base_required_unreadable_exit1(self, tmp_path: Path):
        repo = make_repo(tmp_path, "4.12.0")
        assert preflight.main(
            ["--root", str(repo), "--since", "deadbeef", "--base-required"]
        ) == 1


# ---------------------------------------------------------------------------
# v4.19: bounded merge-base fallback for an orphaned/unreadable --since base
# (the force-push orphaning seen in the v4.18 release chain)
# ---------------------------------------------------------------------------

_ORPHAN = "deadbeef" * 5  # 40 hex chars, never a readable ref


def _add_origin_remote(repo: Path, tmp_path: Path) -> None:
    """Give the repo a REAL origin (bare) with main pushed, so the fallback's
    `git fetch origin main` genuinely works instead of being mocked."""
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(bare)],
        check=True, capture_output=True, text=True,
    )
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")


class TestMergeBaseFallback:
    def _repo(self, tmp_path: Path, version: str = "4.12.0", *, evidence: bool = True) -> Path:
        repo = make_repo(tmp_path, version, evidence=evidence)
        _add_origin_remote(repo, tmp_path)
        return repo

    @staticmethod
    def _bump(repo: Path, version: str, *, with_evidence: bool) -> None:
        _write_version_files(repo, version)
        if with_evidence:
            (repo / "release-evidence").mkdir(exist_ok=True)
            (repo / "release-evidence" / f"v{version}.md").write_text(
                _REQUIRED_EVIDENCE.format(v=version), encoding="utf-8"
            )
            allow = (repo / ".publishallow").read_text(encoding="utf-8")
            (repo / ".publishallow").write_text(
                allow + f"release-evidence/v{version}.md\n", encoding="utf-8"
            )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"bump {version}")

    def test_orphaned_base_recovers_via_fallback_with_evidence(self, tmp_path: Path):
        repo = self._repo(tmp_path)  # origin/main = 4.12.0 WITH evidence
        self._bump(repo, "4.13.0", with_evidence=True)
        result = preflight.preflight(repo, since=_ORPHAN, base_required=True)
        assert result.ok, result.errors

    def test_orphaned_base_fallback_catches_bump_without_evidence(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        self._bump(repo, "4.13.0", with_evidence=False)
        result = preflight.preflight(repo, since=_ORPHAN, base_required=True)
        assert not result.ok
        assert any("4.13.0" in e for e in result.errors)

    def test_fallback_verifies_head_evidence_unconditionally(self, tmp_path: Path):
        # main-push bypass shape: origin/main == HEAD, no version delta — the
        # fallback must STILL demand HEAD evidence (merge-base == HEAD must
        # not skip the check).
        repo = self._repo(tmp_path, "4.13.0", evidence=False)
        result = preflight.preflight(repo, since=_ORPHAN, base_required=True)
        assert not result.ok
        assert any("evidence" in e.lower() or "HEAD" in e for e in result.errors)

    def test_fallback_over_bound_fails_closed(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        self._bump(repo, "4.13.0", with_evidence=True)
        (repo / "README.md").write_text(
            (repo / "README.md").read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8"
        )
        _git(repo, "commit", "-aqm", "docs 1")
        (repo / "README.md").write_text(
            (repo / "README.md").read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8"
        )
        _git(repo, "commit", "-aqm", "docs 2")
        result = preflight.preflight(
            repo, since=_ORPHAN, base_required=True, fallback_bound=1
        )
        assert not result.ok
        assert any("exceeds bound" in e for e in result.errors)
        # the same shape passes with the default bound
        assert preflight.preflight(repo, since=_ORPHAN, base_required=True).ok

    def test_fallback_no_common_ancestor_fails_closed(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        _git(repo, "checkout", "-q", "--orphan", "unrelated")
        for path in repo.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                path.unlink()
        (repo / ".publishallow").write_text("# nothing\n", encoding="utf-8")
        self._bump(repo, "4.14.0", with_evidence=True)
        result = preflight.preflight(repo, since=_ORPHAN, base_required=True)
        assert not result.ok
        assert any("no common ancestor" in e for e in result.errors)

    def test_readable_but_not_ancestor_engages_fallback(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        _git(repo, "checkout", "-qb", "diverged")
        (repo / "README.md").write_text("diverged\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "diverge")
        _git(repo, "checkout", "-q", "master")
        # 'diverged' is readable but NOT an ancestor of HEAD: fallback engages
        result = preflight.preflight(repo, since="diverged", base_required=True)
        assert result.ok, result.errors  # no version delta + evidence present

    def test_fallback_fetch_failure_fails_closed(self, tmp_path: Path):
        # no origin remote at all: fetch fails -> fail closed with diagnostics
        repo = make_repo(tmp_path, "4.12.0")
        result = preflight.preflight(repo, since=_ORPHAN, base_required=True)
        assert not result.ok
        assert any("fallback refused" in e for e in result.errors)
