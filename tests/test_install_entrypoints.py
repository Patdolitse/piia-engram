"""Install/launch truth guard (Node N7).

A command-based smoke proof that the *installed* surface a new user is told to
use actually resolves: every console script declared in ``pyproject.toml``
points at an importable ``module:function`` target, and the documented
``python -m piia_engram.mcp_server`` launch path exposes a callable ``main``.

This guards the install/launch runbook against drift — if an entry point is
renamed or a target removed, the audit fails instead of a new user hitting a
broken command. It does NOT assert any real GUI integration (that is only
expected-to-work and is documented as such in the runbook).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _ROOT / "pyproject.toml"

# The launch paths the runbook promises a new user.
_EXPECTED_SCRIPTS = {"piia-engram", "piia-engram-mcp", "engram"}
_DOCUMENTED_MODULE_LAUNCH = "piia_engram.mcp_server"


def _load_scripts() -> dict[str, str]:
    """Return the ``[project.scripts]`` table from pyproject.toml.

    Uses ``tomllib`` (3.11+) when available, else a minimal section parser so
    the test runs on the declared floor of Python 3.10.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    try:
        import tomllib  # type: ignore[import-not-found]

        return tomllib.loads(text).get("project", {}).get("scripts", {})
    except ModuleNotFoundError:  # pragma: no cover - only on 3.10
        scripts: dict[str, str] = {}
        in_section = False
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                in_section = line == "[project.scripts]"
                continue
            if in_section and "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                scripts[key.strip()] = value.strip().strip('"').strip("'")
        return scripts


def test_declared_scripts_match_expected_surface():
    scripts = _load_scripts()
    assert _EXPECTED_SCRIPTS.issubset(set(scripts)), (
        f"pyproject [project.scripts] is missing expected entry points: "
        f"{_EXPECTED_SCRIPTS - set(scripts)}"
    )


@pytest.mark.parametrize("script_name", sorted(_EXPECTED_SCRIPTS))
def test_console_script_target_resolves(script_name):
    """Each console script resolves to an importable callable target."""
    scripts = _load_scripts()
    target = scripts[script_name]
    assert ":" in target, f"{script_name} target must be 'module:function', got {target!r}"
    module_name, func_name = target.split(":", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    assert callable(func), f"{target} does not resolve to a callable"


def test_documented_module_launch_has_main():
    """`python -m piia_engram.mcp_server` path exposes a callable main()."""
    module = importlib.import_module(_DOCUMENTED_MODULE_LAUNCH)
    assert callable(getattr(module, "main", None)), (
        f"{_DOCUMENTED_MODULE_LAUNCH}.main must be callable for the documented "
        "`python -m` launch path"
    )
