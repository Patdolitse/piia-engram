"""OpenClaw L3 static-bridge conformance tests.

These tests intentionally cover the static file bridge only:
SOUL.md / USER.md / MEMORY.md. They do not claim live-agent or provider
integration.
"""

from __future__ import annotations

from pathlib import Path

from piia_engram.compat import (
    OPENCLAW_BRIDGE_LEVEL,
    OPENCLAW_MEMORY_MAX_BYTES,
    export_to_openclaw,
    import_from_openclaw,
)
from piia_engram.core import Engram


def test_openclaw_l3_export_declares_static_bridge_level(tmp_path: Path):
    engram = Engram(root=tmp_path / "engram")
    engram.update_profile({"role": "synthetic developer", "language": "English"})

    result = export_to_openclaw(engram, str(tmp_path / "openclaw"))

    assert result["status"] == "success"
    assert result["bridge_level"] == OPENCLAW_BRIDGE_LEVEL
    assert {Path(path).name for path in result["files"]} == {
        "SOUL.md",
        "USER.md",
        "MEMORY.md",
    }


def test_openclaw_l3_memory_snapshot_is_verified_active_and_bounded(tmp_path: Path):
    engram = Engram(root=tmp_path / "engram")
    engram.add_lesson({"summary": "verified active lesson", "tier": "verified"})
    engram.add_lesson({"summary": "staging lesson must stay out", "tier": "staging"})
    archived = engram.add_lesson({"summary": "archived lesson must stay out"})
    engram.update_lesson(archived["id"], {"status": "archived"})

    result = export_to_openclaw(engram, str(tmp_path / "openclaw"))
    memory = (tmp_path / "openclaw" / "MEMORY.md").read_text(encoding="utf-8")

    assert result["bridge_level"] == OPENCLAW_BRIDGE_LEVEL
    assert "verified active lesson" in memory
    assert "staging lesson must stay out" not in memory
    assert "archived lesson must stay out" not in memory
    assert len(memory.encode("utf-8")) <= OPENCLAW_MEMORY_MAX_BYTES


def test_openclaw_l3_import_declares_static_bridge_level(tmp_path: Path):
    engram = Engram(root=tmp_path / "engram")
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# MEMORY\n\n## Lessons Learned\n- [testing] use synthetic fixtures\n",
        encoding="utf-8",
    )

    result = import_from_openclaw(engram, memory_path=str(memory_file))

    assert result["status"] == "success"
    assert result["bridge_level"] == OPENCLAW_BRIDGE_LEVEL
    summaries = [item.get("summary", "") for item in engram.get_lessons(limit=None)]
    assert "use synthetic fixtures" in summaries
