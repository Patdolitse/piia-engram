"""Smoke tests for the client-validation scaffold CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_scaffold_zero_pollution_is_pending_before_real_run(tmp_path: Path):
    snapshot = tmp_path / "lessons.json"
    snapshot.write_text("[]", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_client_validation.py"),
            "--run-root",
            str(tmp_path / "runs"),
            "--client-id",
            "smoke",
            "--snapshot-file",
            str(snapshot),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    run_dir = Path(json.loads(proc.stdout)["run_dir"])

    zero_text = (run_dir / "zero_pollution.txt").read_text(encoding="utf-8")
    zero_json = json.loads((run_dir / "parsed" / "zero_pollution.json").read_text(encoding="utf-8"))

    assert "状态：待补充" in zero_text
    assert "不能声称通过" in zero_text
    assert zero_json["status"] == "pending"
    assert zero_json["clean"] is None


def test_scaffold_marks_external_workspace_as_not_isolated(tmp_path: Path):
    external_workspace = tmp_path / "external-workspace"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_client_validation.py"),
            "--run-root",
            str(tmp_path / "runs"),
            "--client-id",
            "smoke",
            "--workspace",
            str(external_workspace),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    run_dir = Path(json.loads(proc.stdout)["run_dir"])
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))

    assert meta["workspace_isolated"] is False


def test_scaffold_marks_external_client_home_as_not_isolated(tmp_path: Path):
    external_home = tmp_path / "real-client-home"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_client_validation.py"),
            "--run-root",
            str(tmp_path / "runs"),
            "--client-id",
            "smoke",
            "--copied-client-home",
            str(external_home),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    run_dir = Path(json.loads(proc.stdout)["run_dir"])
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))

    assert meta["home_isolated"] is False


def test_scaffold_marks_client_home_under_run_dir_as_isolated(tmp_path: Path):
    run_root = tmp_path / "runs"
    copied_home = run_root / "smoke-home"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_client_validation.py"),
            "--run-root",
            str(run_root),
            "--client-id",
            "smoke",
            "--copied-client-home",
            str(copied_home),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    run_dir = Path(json.loads(proc.stdout)["run_dir"])
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))

    assert meta["home_isolated"] is True
