"""Tests for the GUI-ready read-only management surface (Task 5, B+).

Pin the surface's promises: building it does not change a single byte of the
store, the schema is closed, the capability contract declares read-only / no
exposed mutations / no network listener, and no body/path/secret leaks.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import management_surface_harness as harness  # noqa: E402
from piia_engram.core import Engram  # noqa: E402
from piia_engram.management_view import (  # noqa: E402
    READONLY_SURFACE_KEYS,
    assert_readonly_surface_closed,
    build_readonly_management_surface,
)

SECRET = "ZZ_SURFACE_SECRET_TOKEN"


def _fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def test_harness_overall_passes(tmp_path: Path):
    report = harness.run_harness(tmp_path / "base")
    assert report["overall_passed"] is True
    assert report["store_unchanged"] is True
    assert report["read_only_contract"] is True
    assert report["no_network_listener"] is True
    assert report["no_secret_leak"] is True
    assert report["no_body_keys"] is True


def test_building_surface_does_not_mutate_store(tmp_path: Path):
    eng = Engram(root=tmp_path / "store")
    eng.add_lesson(f"{SECRET} lesson", tier="staging")
    eng.add_playbook({"title": "p", "steps": ["one"]})
    before = _fingerprint(tmp_path / "store")
    build_readonly_management_surface(eng, project_folder=str(tmp_path / "store"))
    after = _fingerprint(tmp_path / "store")
    assert before == after


def test_surface_closed_schema_and_capabilities(tmp_path: Path):
    eng = Engram(root=tmp_path / "store")
    eng.add_lesson(f"{SECRET} lesson", tier="staging")
    surface = build_readonly_management_surface(eng, project_folder=str(tmp_path / "store"))

    assert set(surface) == set(READONLY_SURFACE_KEYS)
    assert assert_readonly_surface_closed(surface) is surface
    caps = surface["capabilities"]
    assert caps["read_only"] is True
    assert caps["exposed_mutations"] == []
    assert caps["network_listener"] is False
    assert caps["transport"] == "in_process_return_value"


def test_surface_is_metadata_only_no_leak(tmp_path: Path):
    eng = Engram(root=tmp_path / "store")
    eng.add_lesson(
        f"{SECRET} lesson summary",
        detail=f"{SECRET} lesson detail",
        tier="staging",
    )
    eng.add_playbook({
        "title": f"{SECRET} playbook title",
        "steps": [f"{SECRET} step"],
        "scope_type": "project",
        "project_folder": str(tmp_path / "store"),
    })
    surface = build_readonly_management_surface(eng, project_folder=str(tmp_path / "store"))
    rendered = json.dumps(surface, ensure_ascii=False, sort_keys=True)
    assert SECRET not in rendered
    assert str(tmp_path / "store") not in rendered


def test_version_chain_panel_is_counts_only(tmp_path: Path):
    eng = Engram(root=tmp_path / "store")
    surface = build_readonly_management_surface(eng)
    chains = surface["version_chains"]
    assert set(chains) == {"topic_count", "head_count", "superseded_count", "cycle_count"}
    for value in chains.values():
        assert isinstance(value, int)


def test_assert_closed_rejects_exposed_mutation():
    bad = {
        "schema": 1,
        "generated_at": "t",
        "read_only": True,
        "capabilities": {
            "read_only": True,
            "exposed_mutations": ["delete"],  # not allowed
            "advisory_actions": {},
            "network_listener": False,
            "transport": "in_process_return_value",
        },
        "view": {},
        "version_chains": {
            "topic_count": 0,
            "head_count": 0,
            "superseded_count": 0,
            "cycle_count": 0,
        },
    }
    with pytest.raises(AssertionError):
        assert_readonly_surface_closed(bad)


def test_assert_closed_rejects_network_listener():
    bad = {
        "schema": 1,
        "generated_at": "t",
        "read_only": True,
        "capabilities": {
            "read_only": True,
            "exposed_mutations": [],
            "advisory_actions": {},
            "network_listener": True,  # not allowed
            "transport": "in_process_return_value",
        },
        "view": {},
        "version_chains": {
            "topic_count": 0,
            "head_count": 0,
            "superseded_count": 0,
            "cycle_count": 0,
        },
    }
    with pytest.raises(AssertionError):
        assert_readonly_surface_closed(bad)


def test_temp_dir_only_no_real_store(tmp_path, monkeypatch):
    sentinel = tmp_path / "REAL_ENGRAM_MUST_NOT_BE_TOUCHED"
    monkeypatch.setenv("ENGRAM_DIR", str(sentinel))
    harness.run_harness(tmp_path / "base")
    assert not sentinel.exists()
