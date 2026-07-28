from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.storage import _legacy_project_id, _project_id


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path / "store")


def _fake_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


def _fake_worktree(path: Path, common_git_dir: Path, name: str = "wt") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    worktree_git_dir = common_git_dir / "worktrees" / name
    worktree_git_dir.mkdir(parents=True, exist_ok=True)
    (worktree_git_dir / "commondir").write_text("../..", encoding="utf-8")
    (path / ".git").write_text(
        f"gitdir: {worktree_git_dir.resolve()}",
        encoding="utf-8",
    )
    return path


def test_resume_pack_uses_exact_project_scope_for_nested_and_adjacent_projects(
    tmp_path: Path,
) -> None:
    eng = _eng(tmp_path)
    workspace = tmp_path / "workspace"
    parent = workspace / "parent"
    nested = _fake_repo(parent / "engram")
    adjacent = _fake_repo(workspace / "private-adjacent")
    parent.mkdir(parents=True, exist_ok=True)

    eng.add_lesson("Nested project exact lesson", tier="verified", project_folder=str(nested))
    eng.add_lesson("Parent workspace should not leak", tier="verified", project_folder=str(parent))
    eng.add_lesson("Adjacent private project should not leak", tier="verified", project_folder=str(adjacent))
    eng.add_lesson("Global reusable lesson should not enter project resume", tier="verified")
    eng.add_lesson("Nested staging candidate", tier="staging", project_folder=str(nested))

    pack = eng.build_project_resume_pack(project_folder=str(nested), knowledge_limit=10)
    trusted = json.dumps(pack["trusted_context"], ensure_ascii=False)
    review = json.dumps(pack["review_needed"], ensure_ascii=False)
    blob = trusted + review

    assert "Nested project exact lesson" in trusted
    assert "Nested staging candidate" in review
    assert "Parent workspace should not leak" not in blob
    assert "Adjacent private project should not leak" not in blob
    assert "Global reusable lesson should not enter project resume" not in blob
    assert pack["pack_meta"]["scope"]["mode"] == "project_exact"
    assert pack["pack_meta"]["omitted_category_counts"]["lesson:global_excluded_by_exact_scope"] == 1


def test_same_git_common_dir_worktree_can_resume_digest(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    main = _fake_repo(tmp_path / "main")
    worktree = _fake_worktree(tmp_path / "linked-worktree", main / ".git")

    assert _project_id(str(main)) == _project_id(str(worktree))

    eng.save_agent_context(
        "codex",
        (
            "Goal: harden resume scope.\n"
            "Completed: implemented exact project scope.\n"
            "Next: verify linked worktree resume.\n"
        ),
        session_id="scope-session",
        project_folder=str(main),
    )

    pack = eng.build_project_resume_pack(project_folder=str(worktree), digest_limit=1)

    assert pack["pack_meta"]["digest_count"] == 1
    assert pack["handoff"]["current_focus"] == "verify linked worktree resume."
    assert any(
        "implemented exact project scope" in item
        for item in pack["handoff"]["last_completed"]
    )


def test_same_git_common_dir_worktree_reads_canonical_checkpoint(
    tmp_path: Path,
) -> None:
    eng = _eng(tmp_path)
    main = _fake_repo(tmp_path / "checkpoint-main")
    worktree = _fake_worktree(
        tmp_path / "checkpoint-worktree",
        main / ".git",
        name="checkpoint-wt",
    )
    eng.save_project_snapshot(
        str(main),
        {
            "current_state": {
                "last_completed": ["worktree-compatible checkpoint"],
                "next_actions": ["continue in linked checkout"],
            },
        },
        source_tool="codex",
        source_session="checkpoint-session",
    )

    fresh = Engram(root=eng.root)
    pack = fresh.build_project_resume_pack(project_folder=str(worktree))

    assert pack["handoff"]["last_completed"] == [
        "worktree-compatible checkpoint"
    ]
    assert pack["handoff"]["next_actions"] == ["continue in linked checkout"]
    assert pack["freshness"]["status"] == "current"
    assert pack["pack_meta"]["scope"]["project_id"] == _project_id(str(main))


def test_legacy_path_hash_snapshot_is_read_compatible_without_rebinding(
    tmp_path: Path,
) -> None:
    eng = _eng(tmp_path)
    repo = _fake_repo(tmp_path / "repo")
    canonical = _project_id(str(repo))
    legacy = _legacy_project_id(str(repo))
    assert canonical != legacy

    legacy_file = eng._projects_dir / f"{legacy}.json"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        json.dumps({"title": "Legacy Snapshot", "project_folder": str(repo)}),
        encoding="utf-8",
    )

    snapshot = eng.get_project_snapshot(str(repo))

    assert snapshot["title"] == "Legacy Snapshot"
    assert not (eng._projects_dir / f"{canonical}.json").exists()


def test_first_canonical_save_preserves_legacy_snapshot_fields(
    tmp_path: Path,
) -> None:
    eng = _eng(tmp_path)
    repo = _fake_repo(tmp_path / "legacy-save-repo")
    canonical = _project_id(str(repo))
    legacy = _legacy_project_id(str(repo))
    legacy_file = eng._projects_dir / f"{legacy}.json"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        json.dumps(
            {
                "title": "Legacy Snapshot",
                "current_state": {
                    "last_completed": ["legacy completion"],
                    "next_actions": ["legacy next action"],
                },
                "project_folder": str(repo),
            }
        ),
        encoding="utf-8",
    )

    eng.save_project_snapshot(str(repo), {"notes": ["new canonical note"]})

    snapshot = eng.get_project_snapshot(str(repo))
    assert snapshot["title"] == "Legacy Snapshot"
    assert snapshot["current_state"]["last_completed"] == ["legacy completion"]
    assert snapshot["notes"] == ["new canonical note"]
    assert (eng._projects_dir / f"{canonical}.json").exists()
    assert legacy_file.exists()


def test_worktree_reads_main_checkout_legacy_snapshot(tmp_path: Path) -> None:
    eng = _eng(tmp_path)
    main = _fake_repo(tmp_path / "legacy-main")
    worktree = _fake_worktree(
        tmp_path / "legacy-worktree",
        main / ".git",
        name="legacy-alias",
    )
    legacy = _legacy_project_id(str(main))
    legacy_file = eng._projects_dir / f"{legacy}.json"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        json.dumps(
            {
                "title": "Main checkout legacy snapshot",
                "project_folder": str(main),
            }
        ),
        encoding="utf-8",
    )

    snapshot = eng.get_project_snapshot(str(worktree))

    assert snapshot["title"] == "Main checkout legacy snapshot"
