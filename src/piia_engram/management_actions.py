"""Structured metadata-only management actions for GUI consumers."""

from __future__ import annotations

from typing import Any


ACTION_KEYS = frozenset(
    {
        "schema",
        "target",
        "action",
        "id",
        "confirmed",
        "dry_run",
        "requires_confirmation",
        "changed",
        "status",
        "result",
        "error",
    }
)


def _closed_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != ACTION_KEYS:
        missing = sorted(ACTION_KEYS - set(payload))
        extra = sorted(set(payload) - ACTION_KEYS)
        raise AssertionError(f"management action schema drift: missing={missing} extra={extra}")
    return payload


def _payload(
    *,
    target: str,
    action: str,
    item_id: str,
    confirm: bool,
    dry_run: bool,
    requires_confirmation: bool,
    changed: bool,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return _closed_payload({
        "schema": 1,
        "target": target,
        "action": action,
        "id": item_id,
        "confirmed": bool(confirm),
        "dry_run": bool(dry_run),
        "requires_confirmation": bool(requires_confirmation),
        "changed": bool(changed),
        "status": status,
        "result": result or {},
        "error": error,
    })


def _review_item(eng, item_id: str) -> tuple[str | None, dict[str, Any] | None]:
    item_type, item = eng._find_item_by_id(item_id)
    if item_type not in {"lesson", "decision"} or not isinstance(item, dict):
        return None, None
    return item_type, item


def _playbook_item(eng, item_id: str) -> dict[str, Any] | None:
    item = eng._read_playbook_by_id(item_id)
    return item if isinstance(item, dict) else None


def _status(value: object, default: str = "active") -> str:
    return str(value or default).strip().lower() or default


def _review_transition(kind: str, item: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "approve":
        return {
            "kind": kind,
            "from_tier": str(item.get("tier") or ""),
            "to_tier": "verified",
        }
    return {
        "kind": kind,
        "from_status": _status(item.get("status")),
        "to_status": "outdated",
    }


def _run_review_action(
    eng,
    *,
    action: str,
    item_id: str,
    confirm: bool,
) -> dict[str, Any]:
    if action not in {"approve", "archive"}:
        return _payload(
            target="review",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_action",
            error="invalid_action",
        )

    kind, item = _review_item(eng, item_id)
    if item is None or kind is None:
        return _payload(
            target="review",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="not_found",
            error="item_not_found",
        )

    result = _review_transition(kind, item, action)
    if action == "approve" and item.get("tier") != "staging":
        return _payload(
            target="review",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_state",
            result=result,
            error="review_item_not_staging",
        )

    if not confirm:
        return _payload(
            target="review",
            action=action,
            item_id=item_id,
            confirm=False,
            dry_run=True,
            requires_confirmation=True,
            changed=False,
            status="confirmation_required",
            result=result,
        )

    if action == "approve":
        applied = eng.promote_knowledge(item_id)
        ok = applied.get("status") == "promoted"
        return _payload(
            target="review",
            action=action,
            item_id=item_id,
            confirm=True,
            dry_run=False,
            requires_confirmation=False,
            changed=ok,
            status="applied" if ok else "not_found",
            result=result,
            error=None if ok else "item_not_found",
        )

    applied = eng.archive_knowledge(item_id)
    ok = not bool(applied.get("error"))
    return _payload(
        target="review",
        action=action,
        item_id=item_id,
        confirm=True,
        dry_run=False,
        requires_confirmation=False,
        changed=ok,
        status="applied" if ok else "failed",
        result=result,
        error=None if ok else "archive_failed",
    )


def _playbook_transition(item: dict[str, Any], action: str) -> dict[str, Any]:
    current = _status(item.get("status"))
    if current == "outdated":
        # Public management contract uses "archived" while core stores the
        # legacy status value "outdated".
        current = "archived"
    if action == "delete":
        target = "deleted"
    elif action == "restore":
        target = "active"
    else:
        target = "archived"
    return {
        "kind": "playbook",
        "from_status": current,
        "to_status": target,
    }


def _scope_type(item: dict[str, Any]) -> str:
    scope = item.get("scope")
    if isinstance(scope, dict):
        value = str(scope.get("type") or "global").strip().lower()
        return value or "global"
    return "global"


def _playbook_scope_transition(
    item: dict[str, Any],
    action: str,
    project_folders: list[str] | None = None,
) -> dict[str, Any]:
    if action == "accept_project":
        target = "project"
    elif action == "accept_shared":
        target = "shared"
    elif action == "skip":
        target = "skipped"
    else:
        target = "global"
    result = {
        "kind": "playbook_scope",
        "from_scope": _scope_type(item),
        "to_scope": target,
    }
    if target == "shared":
        result["project_count"] = len([folder for folder in (project_folders or []) if str(folder).strip()])
    return result


def _run_playbook_scope_action(
    eng,
    *,
    action: str,
    item_id: str,
    confirm: bool,
    project_folder: str = "",
    project_folders: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if action not in {"accept_global", "accept_project", "accept_shared", "skip"}:
        return _payload(
            target="playbook_scope",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_action",
            error="invalid_action",
        )

    item = _playbook_item(eng, item_id)
    if item is None:
        return _payload(
            target="playbook_scope",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="not_found",
            error="item_not_found",
        )
    result = _playbook_scope_transition(item, action, project_folders=project_folders)

    if action == "accept_project" and not str(project_folder or "").strip():
        return _payload(
            target="playbook_scope",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_state",
            result=result,
            error="project_folder_required",
        )
    if action == "accept_shared" and not [
        folder for folder in (project_folders or []) if str(folder).strip()
    ]:
        return _payload(
            target="playbook_scope",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_state",
            result=result,
            error="project_folders_required",
        )

    if not confirm:
        return _payload(
            target="playbook_scope",
            action=action,
            item_id=item_id,
            confirm=False,
            dry_run=True,
            requires_confirmation=True,
            changed=False,
            status="confirmation_required",
            result=result,
        )

    applied = eng.resolve_playbook_scope_review(
        item_id,
        action=action,
        project_folder=project_folder,
        project_folders=project_folders,
        note=reason,
        dry_run=False,
        confirm=True,
    )
    ok = not bool(applied.get("error"))
    return _payload(
        target="playbook_scope",
        action=action,
        item_id=item_id,
        confirm=True,
        dry_run=False,
        requires_confirmation=False,
        changed=ok,
        status="applied" if ok else "failed",
        result=result,
        error=None if ok else str(applied.get("error") or "scope_action_failed"),
    )


def _run_playbook_action(
    eng,
    *,
    action: str,
    item_id: str,
    confirm: bool,
    reason: str = "",
) -> dict[str, Any]:
    if action not in {"archive", "delete", "restore"}:
        return _payload(
            target="playbook",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="invalid_action",
            error="invalid_action",
        )

    item = _playbook_item(eng, item_id)
    if item is None:
        return _payload(
            target="playbook",
            action=action,
            item_id=item_id,
            confirm=confirm,
            dry_run=True,
            requires_confirmation=False,
            changed=False,
            status="not_found",
            error="item_not_found",
        )
    result = _playbook_transition(item, action)

    if not confirm:
        return _payload(
            target="playbook",
            action=action,
            item_id=item_id,
            confirm=False,
            dry_run=True,
            requires_confirmation=True,
            changed=False,
            status="confirmation_required",
            result=result,
        )

    if action == "delete":
        applied = eng.delete_playbook(
            item_id,
            reason=reason,
            dry_run=False,
            confirm=True,
        )
        ok = not bool(applied.get("error"))
    elif action == "restore":
        applied = eng.restore_playbook(item_id, dry_run=False, confirm=True)
        ok = not bool(applied.get("error"))
    else:
        applied = eng.archive_playbook(item_id)
        ok = not bool(applied.get("error"))

    return _payload(
        target="playbook",
        action=action,
        item_id=item_id,
        confirm=True,
        dry_run=False,
        requires_confirmation=False,
        changed=ok,
        status="applied" if ok else "failed",
        result=result,
        error=None if ok else "action_failed",
    )


def run_management_action(
    eng,
    *,
    target: str,
    action: str,
    item_id: str,
    confirm: bool = False,
    project_folder: str = "",
    project_folders: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Run one metadata-only management action.

    This is intentionally a wrapper around existing core write methods. It
    never returns the core object because those objects can contain titles,
    bodies, steps, reasoning, or user paths.
    """
    target = str(target or "").strip().lower()
    action = str(action or "").strip().lower()
    item_id = str(item_id or "").strip()
    if target == "review":
        return _run_review_action(
            eng,
            action=action,
            item_id=item_id,
            confirm=confirm,
        )
    if target == "playbook":
        return _run_playbook_action(
            eng,
            action=action,
            item_id=item_id,
            confirm=confirm,
            reason=reason,
        )
    if target == "playbook_scope":
        return _run_playbook_scope_action(
            eng,
            action=action,
            item_id=item_id,
            confirm=confirm,
            project_folder=project_folder,
            project_folders=project_folders,
            reason=reason,
        )
    return _payload(
        target=target,
        action=action,
        item_id=item_id,
        confirm=confirm,
        dry_run=True,
        requires_confirmation=False,
        changed=False,
        status="invalid_target",
        error="invalid_target",
    )


def render_management_action_text(payload: dict[str, Any]) -> str:
    """Render a compact action receipt without content payloads."""
    changed = "changed" if payload.get("changed") else "no-change"
    return (
        "Engram management action\n"
        f"  status: {payload.get('status')}\n"
        f"  target: {payload.get('target')}\n"
        f"  action: {payload.get('action')}\n"
        f"  id: {payload.get('id')}\n"
        f"  result: {changed}\n"
    )
