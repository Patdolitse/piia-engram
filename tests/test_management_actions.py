"""Structured management action contract tests."""

from __future__ import annotations

import json
from pathlib import Path


SECRET = "ZZ_MANAGEMENT_ACTION_SECRET"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _all_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def _assert_action_metadata_only(payload: dict) -> None:
    rendered = _dump(payload)
    assert SECRET not in rendered
    assert not (
        _all_keys(payload)
        & {
            "summary",
            "detail",
            "body",
            "content",
            "reasoning",
            "question",
            "choice",
            "title",
            "description",
            "triggers",
            "steps",
            "project_folder",
            "raw_path",
        }
    )


def test_review_approve_requires_confirmation_and_has_zero_side_effect(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    lesson = eng.add_lesson(
        f"{SECRET} summary",
        detail=f"{SECRET} detail",
        tier="staging",
    )
    before = (tmp_path / "knowledge" / "lessons.json").read_bytes()

    result = run_management_action(
        eng,
        target="review",
        action="approve",
        item_id=lesson["id"],
        confirm=False,
    )

    assert result["schema"] == 1
    assert result["status"] == "confirmation_required"
    assert result["dry_run"] is True
    assert result["requires_confirmation"] is True
    assert result["changed"] is False
    assert result["result"] == {
        "kind": "lesson",
        "from_tier": "staging",
        "to_tier": "verified",
    }
    assert (tmp_path / "knowledge" / "lessons.json").read_bytes() == before
    _assert_action_metadata_only(result)


def test_review_approve_confirmed_promotes_without_body_echo(tmp_path: Path) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    lesson = eng.add_lesson(
        f"{SECRET} summary",
        detail=f"{SECRET} detail",
        tier="staging",
    )

    result = run_management_action(
        eng,
        target="review",
        action="approve",
        item_id=lesson["id"],
        confirm=True,
    )

    assert result["status"] == "applied"
    assert result["dry_run"] is False
    assert result["requires_confirmation"] is False
    assert result["changed"] is True
    assert result["result"]["kind"] == "lesson"
    assert result["result"]["to_tier"] == "verified"
    stored = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert stored[0]["tier"] == "verified"
    _assert_action_metadata_only(result)


def test_playbook_delete_and_restore_actions_are_metadata_only(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    playbook = eng.add_playbook({
        "title": f"{SECRET} playbook title",
        "description": f"{SECRET} description",
        "triggers": [f"{SECRET} trigger"],
        "steps": [f"{SECRET} step"],
    })

    deleted = run_management_action(
        eng,
        target="playbook",
        action="delete",
        item_id=playbook["id"],
        confirm=True,
        reason=f"{SECRET} reason",
    )
    restored = run_management_action(
        eng,
        target="playbook",
        action="restore",
        item_id=playbook["id"],
        confirm=True,
    )

    assert deleted["status"] == "applied"
    assert deleted["result"] == {
        "kind": "playbook",
        "from_status": "active",
        "to_status": "deleted",
    }
    assert restored["status"] == "applied"
    assert restored["result"] == {
        "kind": "playbook",
        "from_status": "deleted",
        "to_status": "active",
    }
    _assert_action_metadata_only(deleted)
    _assert_action_metadata_only(restored)


def test_review_archive_and_playbook_archive_actions_are_metadata_only(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    decision = eng.add_decision(
        f"{SECRET} decision question",
        choice=f"{SECRET} choice",
        reasoning=f"{SECRET} reasoning",
        tier="staging",
    )
    playbook = eng.add_playbook({
        "title": f"{SECRET} playbook title",
        "description": f"{SECRET} description",
        "steps": [f"{SECRET} step"],
    })

    archived_review = run_management_action(
        eng,
        target="review",
        action="archive",
        item_id=decision["id"],
        confirm=True,
    )
    archived_playbook = run_management_action(
        eng,
        target="playbook",
        action="archive",
        item_id=playbook["id"],
        confirm=True,
    )

    assert archived_review["status"] == "applied"
    assert archived_review["result"] == {
        "kind": "decision",
        "from_status": "active",
        "to_status": "outdated",
    }
    assert archived_playbook["status"] == "applied"
    assert archived_playbook["result"] == {
        "kind": "playbook",
        "from_status": "active",
        "to_status": "archived",
    }
    _assert_action_metadata_only(archived_review)
    _assert_action_metadata_only(archived_playbook)


def test_playbook_scope_accept_project_requires_confirmation_and_is_metadata_only(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    project = str(tmp_path / f"{SECRET}-project")
    playbook = eng.add_playbook({
        "title": f"{SECRET} ambiguous playbook",
        "triggers": [f"{SECRET} trigger"],
        "steps": [f"{SECRET} step"],
    })

    preview = run_management_action(
        eng,
        target="playbook_scope",
        action="accept_project",
        item_id=playbook["id"],
        project_folder=project,
        confirm=False,
    )

    assert preview["status"] == "confirmation_required"
    assert preview["dry_run"] is True
    assert preview["requires_confirmation"] is True
    assert preview["changed"] is False
    assert preview["result"] == {
        "kind": "playbook_scope",
        "from_scope": "global",
        "to_scope": "project",
    }
    stored = eng.get_playbook(playbook["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"
    _assert_action_metadata_only(preview)


def test_playbook_scope_confirmed_actions_resolve_queue_without_body_echo(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    project = str(tmp_path / f"{SECRET}-project")
    project_pb = eng.add_playbook({
        "title": f"{SECRET} project playbook",
        "description": f"{SECRET} description",
        "triggers": [f"{SECRET} trigger"],
        "steps": [f"{SECRET} step"],
    })
    skipped_pb = eng.add_playbook({
        "title": "Unrelated inbox cleanup scope candidate",
        "description": f"{SECRET} skipped description",
        "triggers": ["inbox-cleanup-scope-review"],
        "steps": ["Review ambiguous inbox cleanup workflow."],
    })

    accepted = run_management_action(
        eng,
        target="playbook_scope",
        action="accept_project",
        item_id=project_pb["id"],
        project_folder=project,
        confirm=True,
        reason=f"{SECRET} note",
    )
    skipped = run_management_action(
        eng,
        target="playbook_scope",
        action="skip",
        item_id=skipped_pb["id"],
        confirm=True,
        reason=f"{SECRET} skip reason",
    )

    assert accepted["status"] == "applied"
    assert accepted["result"]["to_scope"] == "project"
    assert skipped["status"] == "applied"
    assert skipped["result"]["to_scope"] == "skipped"
    stored_project = eng.get_playbook(project_pb["id"], _update_access=False)
    stored_skipped = eng.get_playbook(skipped_pb["id"], _update_access=False)
    assert stored_project["scope"]["type"] == "project"
    assert stored_project["scope"]["project_folder"] == project
    assert stored_skipped["scope_review_status"] == "skipped"
    _assert_action_metadata_only(accepted)
    _assert_action_metadata_only(skipped)


def test_playbook_scope_accept_global_confirmed_is_metadata_only(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    playbook = eng.add_playbook({
        "title": f"{SECRET} global playbook",
        "description": f"{SECRET} global description",
        "triggers": ["global-scope-review"],
    })

    result = run_management_action(
        eng,
        target="playbook_scope",
        action="accept_global",
        item_id=playbook["id"],
        confirm=True,
        reason=f"{SECRET} global note",
    )

    assert result["status"] == "applied"
    assert result["result"] == {
        "kind": "playbook_scope",
        "from_scope": "global",
        "to_scope": "global",
    }
    stored = eng.get_playbook(playbook["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"
    assert stored["scope_review_status"] == "resolved"
    _assert_action_metadata_only(result)


def test_playbook_scope_accept_project_requires_project_without_mutation(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    playbook = eng.add_playbook({
        "title": f"{SECRET} projectless playbook",
        "triggers": [f"{SECRET} trigger"],
    })

    result = run_management_action(
        eng,
        target="playbook_scope",
        action="accept_project",
        item_id=playbook["id"],
        confirm=True,
    )

    assert result["status"] == "invalid_state"
    assert result["error"] == "project_folder_required"
    assert result["changed"] is False
    assert eng.get_playbook(playbook["id"], _update_access=False)["scope"]["type"] == "global"
    _assert_action_metadata_only(result)


def test_playbook_scope_accept_project_preview_requires_project_without_mutation(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)
    playbook = eng.add_playbook({
        "title": f"{SECRET} preview missing project",
        "triggers": ["missing-project-preview"],
    })

    result = run_management_action(
        eng,
        target="playbook_scope",
        action="accept_project",
        item_id=playbook["id"],
        confirm=False,
    )

    assert result["status"] == "invalid_state"
    assert result["error"] == "project_folder_required"
    assert result["changed"] is False
    assert result["requires_confirmation"] is False
    assert eng.get_playbook(playbook["id"], _update_access=False)["scope"]["type"] == "global"
    _assert_action_metadata_only(result)


def test_management_action_error_paths_are_structured_and_safe(tmp_path: Path) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import run_management_action

    eng = Engram(root=tmp_path)

    invalid_target = run_management_action(
        eng,
        target="unknown",
        action="approve",
        item_id="missing",
    )
    invalid_action = run_management_action(
        eng,
        target="review",
        action="publish",
        item_id="missing",
    )
    not_found = run_management_action(
        eng,
        target="review",
        action="approve",
        item_id="missing",
    )

    assert invalid_target["status"] == "invalid_target"
    assert invalid_target["error"] == "invalid_target"
    assert invalid_action["status"] == "invalid_action"
    assert invalid_action["error"] == "invalid_action"
    assert not_found["status"] == "not_found"
    assert not_found["error"] == "item_not_found"
    _assert_action_metadata_only(invalid_target)
    _assert_action_metadata_only(invalid_action)
    _assert_action_metadata_only(not_found)


def test_management_action_text_receipt_is_metadata_only(tmp_path: Path) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_actions import (
        render_management_action_text,
        run_management_action,
    )

    eng = Engram(root=tmp_path)
    lesson = eng.add_lesson(
        f"{SECRET} text summary",
        detail=f"{SECRET} text detail",
        tier="staging",
    )

    payload = run_management_action(
        eng,
        target="review",
        action="approve",
        item_id=lesson["id"],
        confirm=False,
    )
    rendered = render_management_action_text(payload)

    assert "Engram management action" in rendered
    assert "confirmation_required" in rendered
    assert lesson["id"] in rendered
    assert SECRET not in rendered


def test_management_action_cli_json_is_structured_and_safe(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_management

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson(
        f"{SECRET} cli summary",
        detail=f"{SECRET} cli detail",
        tier="staging",
    )

    assert run_management(["action", "review", "approve", lesson["id"], "--yes", "--json"]) == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert payload["status"] == "applied"
    assert payload["result"]["to_tier"] == "verified"
    assert SECRET not in out
    _assert_action_metadata_only(payload)


def test_management_action_cli_can_resolve_playbook_scope_without_body_echo(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_management

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project = str(tmp_path / f"{SECRET}-project")
    playbook = Engram().add_playbook({
        "title": f"{SECRET} cli scope title",
        "triggers": [f"{SECRET} scope"],
    })

    assert (
        run_management([
            "action",
            "playbook_scope",
            "accept_project",
            playbook["id"],
            "--project",
            project,
            "--yes",
            "--json",
        ])
        == 0
    )

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert payload["status"] == "applied"
    assert payload["result"]["to_scope"] == "project"
    assert SECRET not in out
    _assert_action_metadata_only(payload)
