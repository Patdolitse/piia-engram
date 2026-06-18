"""Tests for onboard-repo anchor enumeration (M1).

enumerate_anchors() scans a repo's manifests + key files and returns structured
anchor candidates. The existing freshness_anchors helpers only CHECK a single
given ref; this adds enumeration. Anchors reuse the A.5a string form
("dep:<name>" / "file:<path>") via the `ref`, plus version/position detail.
"""
from __future__ import annotations

from pathlib import Path

from piia_engram import freshness_anchors


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "onboard_repo_golden"


def _by_ref(anchors, kind, ref):
    for a in anchors:
        if a.get("kind") == kind and a.get("ref") == ref:
            return a
    return None


def test_enumerate_includes_npm_dependency_with_version():
    anchors = freshness_anchors.enumerate_anchors(str(FIXTURE))
    react = _by_ref(anchors, "dep", "react")
    assert react is not None, f"react dep anchor missing; got {anchors!r}"
    assert react["detail"]["version"] == "^18.2.0"
    assert react["source"] == "package.json"


def test_enumerate_includes_npm_dev_dependency():
    anchors = freshness_anchors.enumerate_anchors(str(FIXTURE))
    jest = _by_ref(anchors, "dep", "jest")
    assert jest is not None, f"jest devDependency missing; got {anchors!r}"
    assert jest["detail"]["version"] == "^29.7.0"
    assert jest["source"] == "package.json"


def test_enumerate_includes_pyproject_dependency_with_spec():
    anchors = freshness_anchors.enumerate_anchors(str(FIXTURE))
    requests = _by_ref(anchors, "dep", "requests")
    assert requests is not None, f"requests pyproject dep missing; got {anchors!r}"
    assert requests["source"] == "pyproject.toml"
    assert "2.31" in requests["detail"]["version"]


def test_enumerate_includes_top_level_file_anchor_with_hash():
    import hashlib

    anchors = freshness_anchors.enumerate_anchors(str(FIXTURE))
    readme = _by_ref(anchors, "file", "README.md")
    assert readme is not None, f"README.md file anchor missing; got {anchors!r}"
    expected = hashlib.sha256((FIXTURE / "README.md").read_bytes()).hexdigest()
    assert readme["detail"]["hash"] == expected


def test_enumerate_marks_unsupported_manifest(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'x'\n[tool.poetry.dependencies]\npython = '^3.10'\n",
        encoding="utf-8",
    )
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    unsupported = [a for a in anchors if a.get("kind") == "unsupported"]
    assert any(a.get("ref") == "pyproject.toml" for a in unsupported), (
        f"unsupported poetry manifest not surfaced; got {anchors!r}"
    )


def test_enumerate_dedup_stable_on_rerun():
    first = freshness_anchors.enumerate_anchors(str(FIXTURE))
    second = freshness_anchors.enumerate_anchors(str(FIXTURE))
    keys = [(a["kind"], a["ref"]) for a in first]
    assert len(keys) == len(set(keys)), f"duplicate (kind,ref) pairs: {keys}"
    assert first == second, "enumeration must be deterministic across runs"


# --- M1 review fixes (Codex) -------------------------------------------------


def test_pyproject_with_poetry_and_project_is_unsupported(tmp_path):
    # poetry/pdm tool table present -> the checker treats the whole file as
    # unsupported; enumerate must match (do NOT yield unverifiable [project] deps).
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\ndependencies = ['requests>=2.31']\n[tool.poetry]\nname = 'x'\n",
        encoding="utf-8",
    )
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    assert not any(a["kind"] == "dep" and a["ref"] == "requests" for a in anchors)
    assert any(a["kind"] == "unsupported" and a["ref"] == "pyproject.toml" for a in anchors)


def test_pyproject_build_system_only_is_not_unsupported(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'setuptools.build_meta'\n",
        encoding="utf-8",
    )
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    assert not any(a["kind"] == "unsupported" for a in anchors), f"over-fired: {anchors!r}"


def test_requirement_with_extras_has_clean_version(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests[security]>=2.31\n", encoding="utf-8")
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    req = _by_ref(anchors, "dep", "requests")
    assert req is not None, f"requests missing; got {anchors!r}"
    assert "[security]" not in req["detail"]["version"]
    assert "2.31" in req["detail"]["version"]


def test_dedup_across_manifests_normalized_prefers_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"Shared.Lib": "^1.0.0"}}', encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text("shared-lib>=2.0\n", encoding="utf-8")
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    deps = [a for a in anchors if a["kind"] == "dep"]
    # both normalize to "shared-lib" -> one entry, package.json wins
    assert len(deps) == 1, f"normalized dedup failed: {deps!r}"
    assert deps[0]["ref"] == "shared-lib"
    assert deps[0]["source"] == "package.json"


def test_enumerate_includes_npm_peer_and_optional(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"peerDependencies": {"peerpkg": "^1"}, "optionalDependencies": {"optpkg": "^2"}}',
        encoding="utf-8",
    )
    anchors = freshness_anchors.enumerate_anchors(str(tmp_path))
    assert _by_ref(anchors, "dep", "peerpkg") is not None
    assert _by_ref(anchors, "dep", "optpkg") is not None


def test_each_anchor_has_storable_anchor_ref():
    anchors = freshness_anchors.enumerate_anchors(str(FIXTURE))
    react = _by_ref(anchors, "dep", "react")
    assert react["anchor_ref"] == "dep:react"
    readme = _by_ref(anchors, "file", "README.md")
    assert readme["anchor_ref"] == "file:README.md"
