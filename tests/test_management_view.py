"""Metadata-only management view tests for GUI/CLI consumers."""

from __future__ import annotations

import json
from pathlib import Path


SECRET = "ZZ_MANAGEMENT_SECRET_TOKEN"


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


def test_management_view_schema_is_closed_and_metadata_only(tmp_path: Path) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_view import build_management_view

    eng = Engram(root=tmp_path)
    lesson = eng.add_lesson(
        f"{SECRET} lesson summary",
        detail=f"{SECRET} lesson detail",
        domain=f"{SECRET} domain",
        tier="staging",
    )
    decision = eng.add_decision(
        f"{SECRET} decision question",
        choice=f"{SECRET} decision choice",
        reasoning=f"{SECRET} decision reasoning",
        tier="staging",
    )
    playbook = eng.add_playbook({
        "title": f"{SECRET} playbook title",
        "description": f"{SECRET} playbook description",
        "triggers": [f"{SECRET} trigger"],
        "steps": [f"{SECRET} step"],
        "domain": f"{SECRET} playbook domain",
        "scope_type": "project",
        "project_folder": str(tmp_path),
    })

    view = build_management_view(eng, project_folder=str(tmp_path))
    rendered = json.dumps(view, ensure_ascii=False, sort_keys=True)

    assert set(view) == {
        "schema",
        "generated_at",
        "storage",
        "continuity",
        "review_queue",
        "playbooks",
        "actions",
    }
    assert view["schema"] == 1
    assert view["storage"] == {
        "kind": "local_json",
        "root_configured": True,
        "network_egress": False,
    }
    assert set(view["review_queue"]) == {
        "pending_count",
        "low_quality_count",
        "items",
    }
    assert set(view["playbooks"]) == {
        "total",
        "active_count",
        "archived_count",
        "deleted_count",
        "scope_review_pending_count",
        "items",
    }
    assert {lesson["id"], decision["id"]} <= {
        item["id"] for item in view["review_queue"]["items"]
    }
    assert playbook["id"] in {item["id"] for item in view["playbooks"]["items"]}
    assert SECRET not in rendered
    assert str(tmp_path) not in rendered
    assert not (
        _all_keys(view)
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


def test_management_view_cli_json_is_metadata_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_management

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    Engram().add_lesson(
        f"{SECRET} cli summary",
        detail=f"{SECRET} cli detail",
        tier="staging",
    )

    assert run_management(["--json", "--project", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert payload["review_queue"]["pending_count"] == 1
    assert SECRET not in out
    assert str(tmp_path) not in out


def test_management_view_text_summarizes_counts_without_payloads(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_management

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    Engram().add_lesson(f"{SECRET} text summary", tier="staging")

    assert run_management([]) == 0

    out = capsys.readouterr().out
    assert "Engram management view" in out
    assert "Review queue: 1 pending" in out
    assert SECRET not in out


def test_management_view_empty_store_and_zero_limits_are_stable(tmp_path: Path) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_view import build_management_view

    view = build_management_view(
        Engram(root=tmp_path),
        project_folder=str(tmp_path),
        review_limit=0,
        playbook_limit=0,
    )

    assert view["review_queue"]["pending_count"] == 0
    assert view["review_queue"]["items"] == []
    assert view["playbooks"]["total"] == 0
    assert view["playbooks"]["items"] == []


def test_management_view_entry_key_contracts_are_runtime_checked(
    tmp_path: Path,
) -> None:
    from piia_engram.core import Engram
    from piia_engram.management_view import (
        PLAYBOOK_ITEM_KEYS,
        REVIEW_ITEM_KEYS,
        build_management_view,
    )

    eng = Engram(root=tmp_path)
    eng.add_lesson("schema candidate", tier="staging")
    eng.add_playbook({"title": "schema playbook", "steps": ["one"]})

    view = build_management_view(eng, project_folder=str(tmp_path))

    assert set(view["review_queue"]["items"][0]) == REVIEW_ITEM_KEYS
    assert set(view["playbooks"]["items"][0]) == PLAYBOOK_ITEM_KEYS
