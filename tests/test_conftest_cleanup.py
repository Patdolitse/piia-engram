"""The suite leaves no engram-collect-* directory behind in the temp dir."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_noop_for_the_child_session():
    """Run by the child pytest session below; does nothing itself."""


def test_a_pytest_session_removes_its_collection_store(tmp_path: Path):
    if os.environ.get("ENGRAM_CLEANUP_CHILD") == "1":
        return  # the child session must not start another one
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ)
    env.update({"TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
                "ENGRAM_CLEANUP_CHILD": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(_ROOT / "src")})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(tmp_path / "child-basetemp"),
         str(Path(__file__)) + "::test_noop_for_the_child_session"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(_ROOT), timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert list(temp.glob("engram-collect-*")) == []
