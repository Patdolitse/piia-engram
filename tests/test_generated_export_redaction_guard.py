"""Tests for the generated export redaction CI guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_generated_export_redaction.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_generated_export_redaction", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_export_redaction_guard_creates_and_scans_surfaces(tmp_path: Path):
    mod = _load_module()

    result = mod.run_guard(tmp_path)

    assert result["ok"] is True
    assert result["root"] == tmp_path.name
    surface_names = {item["surface"] for item in result["surfaces"]}
    assert surface_names == {"identity_card", "knowledge_report", "agents_md"}
    assert all(item["clean"] is True for item in result["surfaces"])
    assert all(item["summary"]["total"] == 0 for item in result["surfaces"])


def test_generated_export_redaction_guard_cli_json(tmp_path: Path, capsys):
    mod = _load_module()

    rc = mod.main(["--work-dir", str(tmp_path), "--json"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out
    assert "sk-proj-" not in out
    assert "victim" not in out
