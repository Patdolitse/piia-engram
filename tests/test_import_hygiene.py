"""Import hygiene: package submodules must import cleanly on a bare first import.

Regression guard for a known cli_commands <-> setup_wizard circular import.
cli_commands.py imports setup_wizard at module top, and setup_wizard re-exports
names from cli_commands at module bottom; importing cli_commands as the VERY
FIRST piia_engram import (as a library user, a script, or a future refactor
might) hit a partially-initialized module and raised ImportError. The normal
entry points (`engram`/`piia-engram` -> setup_wizard:main, MCP -> mcp_server)
avoid it by import order, so the package "worked" while the cycle stayed latent
and several tests had to comment around it. These run in a fresh subprocess so
prior imports in this pytest process can't mask the cycle.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _fresh_import(module: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_cli_commands_bare_first_import():
    r = _fresh_import("piia_engram.cli_commands")
    assert r.returncode == 0, f"bare-first `import piia_engram.cli_commands` failed:\n{r.stderr}"


def test_setup_wizard_bare_first_import():
    r = _fresh_import("piia_engram.setup_wizard")
    assert r.returncode == 0, f"bare-first `import piia_engram.setup_wizard` failed:\n{r.stderr}"
