"""Offline install / upgrade confidence matrix — local-only, no PyPI.

Builds and (optionally) runs a small matrix of confidence checks against
*locally built* artifacts in ``dist/`` — never the network, never an upload:

  1. wheel_install   — fresh temp venv, install the local ``.whl`` with
                       ``pip --no-index --find-links dist`` (no PyPI hit).
  2. sdist_install   — fresh temp venv, install the local ``.tar.gz`` the same way.
  3. import_smoke     — ``python -c "import piia_engram; print(__version__)"`` in
                       each fresh venv.
  4. mcp_boot_smoke   — ``python -m piia_engram.mcp_server --help`` with
                       ``ENGRAM_EPHEMERAL=1`` so booting touches no real store.

The default mode is ``--dry-run``: it prints the planned commands as JSON and
executes nothing, so it is safe to run anywhere (and unit-testable). ``--execute``
runs the plan inside a single temporary base directory that is removed at the
end — it never creates a persistent install outside a temp dir, and never the
user's real environment.

Planning invariants (asserted by the planner and the tests):
- every install command carries ``--no-index`` (no PyPI fallback);
- every artifact path points inside the given ``dist`` dir;
- every venv / work path lives under one base (a temp dir by default);
- the plan never references an index URL, ``twine``, ``upload``, or ``publish``.

Usage (from the repo root)::

    python scripts/offline_install_matrix.py                 # dry-run plan (text)
    python scripts/offline_install_matrix.py --json           # dry-run plan (JSON)
    python scripts/offline_install_matrix.py --execute        # run locally in temp

Exit codes:
- 0  plan built (dry-run) / all steps passed (execute)
- 1  a step failed (execute only)
- 2  no local artifacts found in dist/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

DIST_REL = "dist"
PACKAGE = "piia_engram"

# Substrings that must never appear in a planned command (this is a *local*
# matrix — any of these would mean we reached for the network or a publish path).
_FORBIDDEN_TOKENS = ("pypi.org", "upload", "twine", "publish", "--index-url", "--extra-index-url")


def read_pyproject_version(root: str | Path) -> str:
    """Return ``[project].version`` from the repo's ``pyproject.toml``."""
    path = Path(root) / "pyproject.toml"
    if not path.is_file():
        raise SystemExit(f"[error] pyproject.toml not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"[error] could not parse {path}: {exc}") from exc
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"[error] [project].version missing in {path}")
    return version.strip()


def _artifact_version(path: Path) -> str | None:
    """Extract a version from a wheel or sdist filename."""
    if path.suffix == ".whl":
        m = re.match(r"^[A-Za-z0-9_.]+?-([0-9][^-]*)-", path.name)
        return m.group(1) if m else None
    if path.name.endswith(".tar.gz"):
        m = re.match(r"^[A-Za-z0-9_.]+?-([0-9][^-]*)\.tar\.gz$", path.name)
        return m.group(1) if m else None
    return None


def _version_key(version: str) -> tuple[Any, ...]:
    """A small local version key good enough for Engram's numeric semver tags."""
    parts: list[Any] = []
    for piece in re.split(r"([0-9]+)", version):
        if piece == "":
            continue
        parts.append(int(piece) if piece.isdigit() else piece)
    return tuple(parts)


def _select_artifact(
    artifacts: list[Path],
    *,
    expected_version: str,
    allow_stale: bool,
) -> Path | None:
    matching = [p for p in artifacts if _artifact_version(p) == expected_version]
    if matching:
        return sorted(matching)[-1]
    if not allow_stale or not artifacts:
        return None
    versioned = [(p, _artifact_version(p)) for p in artifacts]
    versioned = [(p, v) for p, v in versioned if v is not None]
    if not versioned:
        return sorted(artifacts)[-1]
    versioned.sort(key=lambda item: (_version_key(item[1] or ""), item[0].name))
    return versioned[-1][0]


def _find_artifacts(
    dist_dir: Path,
    *,
    expected_version: str,
    allow_stale: bool,
) -> dict[str, Path | None]:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    return {
        "wheel": _select_artifact(wheels, expected_version=expected_version,
                                  allow_stale=allow_stale),
        "sdist": _select_artifact(sdists, expected_version=expected_version,
                                  allow_stale=allow_stale),
    }


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _install_step(
    *,
    name: str,
    artifact: Path,
    dist_dir: Path,
    base: Path,
    python: str,
    expected_version: str | None,
) -> dict[str, Any]:
    venv_dir = base / f"venv-{name}"
    vpy = str(_venv_python(venv_dir))
    import_check = (
        f"import {PACKAGE}, sys; "
        f"print(getattr({PACKAGE}, '__version__', 'unknown'))"
    )
    return {
        "name": name,
        "artifact": str(artifact),
        "venv": str(venv_dir),
        "expected_version": expected_version,
        "commands": [
            [python, "-m", "venv", str(venv_dir)],
            [vpy, "-m", "pip", "install", "--no-index",
             "--find-links", str(dist_dir), str(artifact)],
            [vpy, "-c", import_check],
            [vpy, "-m", f"{PACKAGE}.mcp_server", "--help"],
        ],
        # mcp boot smoke runs with an ephemeral store so it never writes a real one.
        "env": {"ENGRAM_EPHEMERAL": "1", "PYTHONIOENCODING": "utf-8"},
    }


def plan_matrix(
    dist_dir: str | Path,
    *,
    base: str | Path | None = None,
    python: str | None = None,
    expected_version: str | None = None,
    allow_stale: bool = False,
) -> dict[str, Any]:
    """Build the offline install matrix plan. Pure: creates no venvs, runs nothing."""
    dist_path = Path(dist_dir).resolve()
    if not dist_path.is_dir():
        raise SystemExit(f"[error] dist dir not found: {dist_path}")
    current_version = expected_version or read_pyproject_version(dist_path.parent)
    artifacts = _find_artifacts(
        dist_path, expected_version=current_version, allow_stale=allow_stale,
    )
    if not artifacts["wheel"] and not artifacts["sdist"]:
        available = sorted(
            {v for p in list(dist_path.glob("*.whl")) + list(dist_path.glob("*.tar.gz"))
             if (v := _artifact_version(p))}
        )
        suffix = f"; available versions: {', '.join(available)}" if available else ""
        raise SystemExit(
            f"[error] no local artifacts for current version {current_version} "
            f"in {dist_path}{suffix}. Build local artifacts first or pass --allow-stale."
        )

    base_path = Path(base).resolve() if base else Path(tempfile.gettempdir()) / "engram-offline-matrix"
    py = python or sys.executable
    selected_versions = {
        _artifact_version(p) for p in artifacts.values() if p is not None
    }
    version = sorted(selected_versions, key=_version_key)[-1] if selected_versions else current_version
    stale = version != current_version

    steps: list[dict[str, Any]] = []
    if artifacts["wheel"]:
        steps.append(_install_step(
            name="wheel_install", artifact=artifacts["wheel"], dist_dir=dist_path,
            base=base_path, python=py, expected_version=version,
        ))
    if artifacts["sdist"]:
        steps.append(_install_step(
            name="sdist_install", artifact=artifacts["sdist"], dist_dir=dist_path,
            base=base_path, python=py, expected_version=version,
        ))

    plan = {
        "schema": 1,
        "matrix": "offline_install_v1",
        "dist_dir": str(dist_path),
        "base": str(base_path),
        "python": py,
        "wheel": str(artifacts["wheel"]) if artifacts["wheel"] else None,
        "sdist": str(artifacts["sdist"]) if artifacts["sdist"] else None,
        "version": version,
        "expected_version": current_version,
        "stale": stale,
        "allow_stale": allow_stale,
        "steps": steps,
        "offline": True,
    }
    _assert_plan_is_local(plan)
    return plan


def _assert_plan_is_local(plan: dict[str, Any]) -> None:
    """Fail loudly if the plan ever reaches for the network or a publish path."""
    base = Path(plan["base"])
    dist = Path(plan["dist_dir"])
    for step in plan["steps"]:
        # venvs must live under the single base dir.
        assert Path(step["venv"]).resolve().is_relative_to(base), step["venv"]
        # the installed artifact must come from the dist dir.
        assert Path(step["artifact"]).resolve().is_relative_to(dist), step["artifact"]
        install_cmds = [c for c in step["commands"] if "install" in c]
        for cmd in install_cmds:
            assert "--no-index" in cmd, f"install without --no-index: {cmd}"
        flat = " ".join(tok for cmd in step["commands"] for tok in cmd).lower()
        for token in _FORBIDDEN_TOKENS:
            assert token not in flat, f"forbidden token {token!r} in plan"


def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Run the plan locally inside ``plan['base']`` (created/cleaned here)."""
    import os

    base = Path(plan["base"])
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        for step in plan["steps"]:
            env = {**os.environ, **step.get("env", {})}
            step_ok = True
            logs: list[dict[str, Any]] = []
            for cmd in step["commands"]:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=env, timeout=300,
                )
                logs.append({"cmd": cmd, "returncode": proc.returncode})
                if proc.returncode != 0:
                    step_ok = False
                    break
                if cmd[:2] == [str(_venv_python(Path(step["venv"]))), "-c"]:
                    observed = (proc.stdout or "").strip().splitlines()[-1:]
                    if not observed or observed[0] != step.get("expected_version"):
                        step_ok = False
                        logs[-1]["version_mismatch"] = {
                            "expected": step.get("expected_version"),
                            "observed": observed[0] if observed else "",
                        }
                        break
            results.append({"name": step["name"], "passed": step_ok, "log": logs})
    finally:
        shutil.rmtree(base, ignore_errors=True)
    passed = all(r["passed"] for r in results)
    return {"results": results, "all_passed": passed}


def render_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Offline install matrix (dry-run) — dist={plan['dist_dir']}",
        f"  version={plan['version']} expected={plan['expected_version']} "
        f"stale={plan['stale']} python={plan['python']}",
        f"  base (temp)={plan['base']}",
    ]
    for step in plan["steps"]:
        lines.append(f"  step {step['name']} (artifact={Path(step['artifact']).name}):")
        for cmd in step["commands"]:
            lines.append("    $ " + " ".join(cmd))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--dist", default=DIST_REL, help="Directory with local artifacts (default: dist/)")
    ap.add_argument("--base", default=None, help="Base temp dir for venvs (default: system temp)")
    ap.add_argument("--allow-stale", action="store_true",
                    help="Allow planning against older local artifacts when current-version artifacts are missing")
    ap.add_argument("--execute", action="store_true", help="Actually run the matrix locally (default: dry-run)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    try:
        plan = plan_matrix(args.dist, base=args.base, allow_stale=args.allow_stale)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise

    if not args.execute:
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(render_text(plan))
        return 0

    outcome = execute_plan(plan)
    if args.json:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        for r in outcome["results"]:
            print(f"  [{'ok' if r['passed'] else '!!'}] {r['name']}")
        print(f"  overall: {'PASS' if outcome['all_passed'] else 'FAIL'}")
    return 0 if outcome["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
