"""Phase 3: local backup-plan helper + data-sovereignty invariants.

These prove the backup plan is metadata-only, never reaches outside the Engram
root, and that producing it (and the existing recovery analysis) never modifies
or deletes EXTERNAL project files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import recovery
from piia_engram.core import Engram


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    root = tmp_path / "engram"
    eng = Engram(root=root)
    eng.add_lesson({"summary": "SENSITIVE lesson body must not appear", "domain": "python"})
    eng.add_decision({"question": "db?", "choice": "SECRET choice body"})
    return root


class TestBackupPlanShape:
    def test_lists_groups_and_datasets(self, seeded: Path):
        plan = recovery.build_backup_plan(seeded)
        assert plan["exists"] is True
        names = {g["name"] for g in plan["groups"]}
        assert "knowledge" in names
        datasets = {d["dataset"] for d in plan["knowledge_datasets"]}
        assert "lessons" in datasets and "decisions" in datasets
        lessons = next(d for d in plan["knowledge_datasets"] if d["dataset"] == "lessons")
        assert lessons["entries"] == 1
        assert plan["total_files"] >= 2
        assert plan["live_store_modified"] is False

    def test_plan_is_metadata_only_no_bodies(self, seeded: Path):
        plan = recovery.build_backup_plan(seeded)
        blob = json.dumps(plan, ensure_ascii=False)
        assert "SENSITIVE lesson body" not in blob
        assert "SECRET choice body" not in blob

    def test_missing_root(self, tmp_path: Path):
        plan = recovery.build_backup_plan(tmp_path / "nope")
        assert plan["exists"] is False
        assert plan["total_files"] == 0
        assert plan["knowledge_datasets"] == []

    def test_external_invariant_field_is_zero(self, seeded: Path):
        plan = recovery.build_backup_plan(seeded)
        assert plan["external_files_included"] == 0


class TestExternalFilesUntouched:
    """Engram local operations must never modify or delete external project files."""

    def test_backup_plan_does_not_touch_external_project(self, seeded: Path, tmp_path: Path):
        external = tmp_path / "some_user_project"
        external.mkdir()
        (external / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (external / "README.md").write_text("# real project\n", encoding="utf-8")
        before = _snapshot(external)

        recovery.build_backup_plan(seeded)
        recovery.analyze_json_recovery_candidates(seeded, dataset="lessons")
        recovery.analyze_recovery_retention_plan(seeded, dataset="lessons")

        after = _snapshot(external)
        assert before == after, "external project files were modified by Engram ops"
        # And nothing was deleted.
        assert (external / "main.py").exists()
        assert (external / "README.md").exists()

    def test_symlink_outside_root_is_excluded(self, seeded: Path, tmp_path: Path):
        outside = tmp_path / "outside.txt"
        outside.write_text("external content\n", encoding="utf-8")
        link = seeded / "knowledge" / "escape_link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted in this environment")
        plan = recovery.build_backup_plan(seeded)
        # The symlink target resolves outside root → excluded, never counted in.
        assert plan["external_files_included"] == 0
        assert plan["external_paths_excluded"] >= 1
