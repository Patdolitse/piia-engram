"""Public release-surface guard.

The release scripts and tracked evidence markers are public maintenance
surfaces. They must not accidentally carry maintainer-local paths or the
gitignored detailed evidence notes that belong outside the repository.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "check_public_release_surface.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_release_surface", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_real_repo_public_release_surface_is_clean():
    mod = _load_module()
    assert mod.scan_public_release_surface(_REPO_ROOT) == []


def test_tracked_release_notes_are_blocked(tmp_path: Path):
    mod = _load_module()
    note = tmp_path / "release-evidence" / "v9.9.9-notes.md"
    note.parent.mkdir()
    note.write_text("private review log", encoding="utf-8")

    hits = mod.scan_public_release_surface(
        tmp_path,
        tracked_files=["release-evidence/v9.9.9-notes.md"],
    )

    assert any(hit["code"] == "tracked_release_notes" for hit in hits)


def test_public_release_script_private_path_is_blocked(tmp_path: Path):
    mod = _load_module()
    script = tmp_path / "scripts" / "release_orchestrator.py"
    script.parent.mkdir()
    planted = "E:" + r"\Temp\mcp-publisher.exe"
    script.write_text(f'DEFAULT = "{planted}"', encoding="utf-8")

    hits = mod.scan_public_release_surface(
        tmp_path,
        tracked_files=["scripts/release_orchestrator.py"],
    )

    assert any(hit["code"] == "private_path" for hit in hits)


def test_marker_evidence_file_must_stay_marker_only(tmp_path: Path):
    mod = _load_module()
    evidence = tmp_path / "release-evidence" / "v9.9.9.md"
    evidence.parent.mkdir()
    planted = "C:" + "/Users/alice/AppData/Local/Temp/review.log"
    evidence.write_text(
        "# Release evidence - v9.9.9\n\n"
        "- self-review: passed\n"
        f"Detailed local run log: {planted}\n",
        encoding="utf-8",
    )

    hits = mod.scan_public_release_surface(
        tmp_path,
        tracked_files=["release-evidence/v9.9.9.md"],
    )

    assert any(hit["code"] == "private_path" for hit in hits)
    assert any(hit["code"] == "non_marker_evidence_line" for hit in hits)
