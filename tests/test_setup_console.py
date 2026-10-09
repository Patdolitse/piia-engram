"""Console encoding regressions."""
import os
import subprocess
import sys


def test_setup_help_reconfigures_gbk_output_to_utf8():
    env = {**os.environ, "PYTHONIOENCODING": "gbk"}
    result = subprocess.run([sys.executable, "-m", "piia_engram.setup_wizard", "--help"],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "核心工具" in result.stdout.decode("utf-8")
