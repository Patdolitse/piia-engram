"""Windows-safe local PyPI fallback upload.

Normal releases should use GitHub Actions + PyPI Trusted Publishing. This
script is only for the fallback path when the CI publish workflow fails after
local artifacts have already passed build, private-term scan, and twine check.

It sets UTF-8 console variables and disables Twine's rich progress bar, avoiding
PowerShell/GBK UnicodeEncodeError crashes seen during v3.50.0.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys


def build_upload_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return env


def build_twine_upload_command(dists: list[str], *, python: str = sys.executable) -> list[str]:
    expanded = expand_dists(dists)
    return [
        python,
        "-m",
        "twine",
        "upload",
        "--disable-progress-bar",
        "--skip-existing",
        *expanded,
    ]


def expand_dists(dists: list[str]) -> list[str]:
    """Expand globs portably without relying on the caller's shell."""
    expanded: list[str] = []
    for item in dists:
        matches = sorted(glob.glob(item))
        expanded.extend(matches or [item])
    return expanded


def upload(dists: list[str], *, python: str = sys.executable, run=subprocess.run) -> int:
    cmd = build_twine_upload_command(dists, python=python)
    proc = run(cmd, env=build_upload_env(), text=True)
    return int(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dists", nargs="*", default=["dist/*"])
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    return upload(args.dists, python=args.python)


if __name__ == "__main__":
    raise SystemExit(main())
