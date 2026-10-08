"""Rejection records dropped by validation during an export are counted as skipped."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from piia_engram import setup_wizard  # noqa: F401  (import order: avoids a cycle)
from piia_engram import cli_commands
from piia_engram.core import Engram

_GOOD = {"id": "t-good", "kind": "lesson", "h1": "a" * 64, "h2": "b" * 64, "hv": 3}
_BAD = {"id": "t-bad", "kind": "lesson", "h1": "private text that is not a hash", "h2": "b" * 64, "hv": 3}


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    Engram(root=root)
    path = root / "knowledge" / "tombstones.jsonl"
    path.write_text(json.dumps(_GOOD) + "\n" + json.dumps(_BAD) + "\n", encoding="utf-8")
    return root


def test_export_summary_counts_skipped_rejections(store, tmp_path):
    summary = Engram(root=store).export_all_with_summary(str(tmp_path / "backup.json"))
    assert summary["skipped"] == {"tombstones": 1}
    backup = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
    assert [t["id"] for t in backup["knowledge"]["tombstones"]] == ["t-good"]
    assert "private text" not in json.dumps(summary)


def test_export_all_still_returns_the_path(store, tmp_path):
    out = Engram(root=store).export_all(str(tmp_path / "b.json"))
    assert isinstance(out, str) and Path(out).is_file()


def test_dock_export_reports_the_skipped_count(store, tmp_path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_commands._run_dock_export(["--json", "--output", str(tmp_path / "dock.json")])
    payload = json.loads(buf.getvalue())
    assert rc == 0 and payload["skipped"] == {"tombstones": 1}
    assert "private text" not in buf.getvalue()


def test_mcp_export_names_the_skipped_count(store, tmp_path, monkeypatch):
    from piia_engram import mcp_server

    eng = Engram(root=store)
    monkeypatch.setattr(mcp_server, "_get_engram", lambda: eng, raising=False)
    import piia_engram.mcp_tools_admin as admin

    monkeypatch.setattr(admin.S, "_get_engram", lambda: eng)
    import asyncio

    out = asyncio.run(admin.export_engram(output_path=str(tmp_path / "mcp.json")))
    assert "导出成功" in out and "1" in out and "skipped 1 malformed" in out
    assert "private text" not in out
