"""Tests for the offline install/upgrade confidence matrix planner.

These exercise the *planning* logic only (no real installs, no network): given a
fake ``dist/`` with a local wheel and sdist, the planner must produce a command
matrix that installs strictly from local artifacts (``--no-index``), boots the
MCP server with an ephemeral store, keeps every venv under one temp base, and
never references PyPI / upload / twine.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "offline_install_matrix.py"


def _load():
    spec = importlib.util.spec_from_file_location("offline_install_matrix", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oim = _load()


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-9.9.9-py3-none-any.whl").write_bytes(b"fake wheel")
    (dist / "piia_engram-9.9.9.tar.gz").write_bytes(b"fake sdist")
    return dist


def test_plan_includes_wheel_and_sdist_steps(fake_dist: Path, tmp_path: Path):
    plan = oim.plan_matrix(fake_dist, base=tmp_path / "base")
    names = {s["name"] for s in plan["steps"]}
    assert names == {"wheel_install", "sdist_install"}
    assert plan["version"] == "9.9.9"
    assert plan["expected_version"] == "9.9.9"
    assert plan["stale"] is False
    assert plan["offline"] is True


def test_install_commands_are_offline(fake_dist: Path, tmp_path: Path):
    plan = oim.plan_matrix(fake_dist, base=tmp_path / "base")
    for step in plan["steps"]:
        install = [c for c in step["commands"] if "install" in c][0]
        assert "--no-index" in install
        assert "--find-links" in install
        assert str(fake_dist.resolve()) in install


def test_plan_has_import_and_mcp_boot_smoke(fake_dist: Path, tmp_path: Path):
    plan = oim.plan_matrix(fake_dist, base=tmp_path / "base")
    step = plan["steps"][0]
    flat = [" ".join(cmd) for cmd in step["commands"]]
    assert any("import piia_engram" in c for c in flat)
    assert any("piia_engram.mcp_server" in c and "--help" in c for c in flat)
    # Boot smoke uses an ephemeral store so it never writes a real one.
    assert step["env"].get("ENGRAM_EPHEMERAL") == "1"


def test_all_venvs_under_single_base(fake_dist: Path, tmp_path: Path):
    base = tmp_path / "base"
    plan = oim.plan_matrix(fake_dist, base=base)
    for step in plan["steps"]:
        assert Path(step["venv"]).resolve().is_relative_to(base.resolve())


def test_plan_never_references_network_or_publish(fake_dist: Path, tmp_path: Path):
    plan = oim.plan_matrix(fake_dist, base=tmp_path / "base")
    flat = " ".join(
        tok for step in plan["steps"] for cmd in step["commands"] for tok in cmd
    ).lower()
    for token in oim._FORBIDDEN_TOKENS:
        assert token not in flat


def test_wheel_only_dist_plans_one_step(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-1.2.3-py3-none-any.whl").write_bytes(b"w")
    plan = oim.plan_matrix(dist, base=tmp_path / "base")
    assert {s["name"] for s in plan["steps"]} == {"wheel_install"}


def test_empty_dist_raises(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(SystemExit):
        oim.plan_matrix(dist, base=tmp_path / "base")


def test_missing_dist_raises(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        oim.plan_matrix(tmp_path / "nope", base=tmp_path / "base")


def test_stale_artifacts_fail_closed_by_default(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-1.0.0-py3-none-any.whl").write_bytes(b"old")
    with pytest.raises(SystemExit) as excinfo:
        oim.plan_matrix(dist, base=tmp_path / "base")
    msg = str(excinfo.value)
    assert "current version 2.0.0" in msg
    assert "1.0.0" in msg
    assert "--allow-stale" in msg


def test_allow_stale_surfaces_staleness(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-1.0.0-py3-none-any.whl").write_bytes(b"old")
    plan = oim.plan_matrix(dist, base=tmp_path / "base", allow_stale=True)
    assert plan["version"] == "1.0.0"
    assert plan["expected_version"] == "2.0.0"
    assert plan["stale"] is True
    assert plan["allow_stale"] is True
    assert "stale=True" in oim.render_text(plan)


def test_stale_selection_uses_parsed_version_not_lexicographic(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "9.0.0"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-3.5.0-py3-none-any.whl").write_bytes(b"old")
    (dist / "piia_engram-3.49.0-py3-none-any.whl").write_bytes(b"newer")
    plan = oim.plan_matrix(dist, base=tmp_path / "base", allow_stale=True)
    assert plan["version"] == "3.49.0"
    assert plan["wheel"].endswith("3.49.0-py3-none-any.whl")


def test_read_pyproject_version_valid_missing_and_malformed(tmp_path: Path):
    with pytest.raises(SystemExit):
        oim.read_pyproject_version(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        oim.read_pyproject_version(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "4.5.6"\n',
        encoding="utf-8",
    )
    assert oim.read_pyproject_version(tmp_path) == "4.5.6"


def test_execute_plan_fails_on_installed_version_mismatch(tmp_path: Path, monkeypatch):
    base = tmp_path / "base"
    venv = base / "venv-wheel_install"
    vpy = oim._venv_python(venv)
    plan = {
        "base": str(base),
        "steps": [{
            "name": "wheel_install",
            "venv": str(venv),
            "expected_version": "2.0.0",
            "commands": [
                [sys.executable, "-m", "venv", str(venv)],
                [str(vpy), "-m", "pip", "install", "--no-index", "artifact.whl"],
                [str(vpy), "-c", "import piia_engram; print(piia_engram.__version__)"],
            ],
            "env": {},
        }],
    }

    class Proc:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        if cmd[:2] == [str(vpy), "-c"]:
            return Proc("1.0.0\n")
        return Proc("")

    monkeypatch.setattr(oim.subprocess, "run", fake_run)
    outcome = oim.execute_plan(plan)
    assert outcome["all_passed"] is False
    log = outcome["results"][0]["log"][-1]
    assert log["version_mismatch"]["expected"] == "2.0.0"
    assert log["version_mismatch"]["observed"] == "1.0.0"


def test_cli_returns_2_for_planning_errors(tmp_path: Path, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "piia-engram"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "piia_engram-1.0.0-py3-none-any.whl").write_bytes(b"old")
    assert oim.main(["--dist", str(dist), "--json"]) == 2
    assert "current version 2.0.0" in capsys.readouterr().err


def test_planner_creates_no_filesystem_side_effects(fake_dist: Path, tmp_path: Path):
    base = tmp_path / "base"
    oim.plan_matrix(fake_dist, base=base)
    # Planning must not create the venv base (only --execute does).
    assert not base.exists()


def test_real_dist_dry_run_plan_is_local_if_present():
    """If the repo's real dist/ has artifacts, the live plan must be local-only."""
    dist = _ROOT / "dist"
    artifacts = list(dist.glob("*.whl")) + list(dist.glob("*.tar.gz")) if dist.is_dir() else []
    if not artifacts:
        pytest.skip("no local artifacts in dist/")
    plan = oim.plan_matrix(dist, allow_stale=True)
    # _assert_plan_is_local already ran inside plan_matrix; re-assert key invariant.
    assert plan["offline"] is True
    assert plan["steps"]
