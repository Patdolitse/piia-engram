"""``python -m piia_engram.setup_wizard`` starts without an import cycle."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_module_entry_help_exits_zero(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
        "ENGRAM_DIR": str(tmp_path / "store"), "ENGRAM_NO_UPDATE_CHECK": "1",
        "PYTHONPATH": _SRC, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
    })
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(
        [sys.executable, "-m", "piia_engram.setup_wizard", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(tmp_path), timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "circular import" not in result.stderr
