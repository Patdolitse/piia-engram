"""Tests for onboard-repo candidate creation + owner accept (M2).

Candidates are agent-proposed STAGING repo-facts (trust-stripped, NOT
auto-verified, NOT self-attested). The owner grants trust later via accept,
which atomically sets tier=verified + stamps confirmation_source="anchor".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


def _by_anchor(eng: Engram, anchor_ref: str) -> dict | None:
    for e in eng.get_lessons(limit=None, _update_access=False):
        if e.get("provenance", {}).get("anchor_ref") == anchor_ref:
            return e
    return None


def test_create_onboard_candidate_is_staging_unconfirmed(eng):
    eng.create_onboard_candidate(
        "This project depends on `react` (^18.2.0).",
        anchor_ref="dep:react",
        anchor_detail={"version": "^18.2.0"},
        anchor_project_id="github.com/acme/app",
        extractor="onboard-repo@test",
    )
    entry = _by_anchor(eng, "dep:react")
    assert entry is not None, "candidate not stored"
    assert entry["tier"] == "staging"            # NOT auto-verified
    prov = entry["provenance"]
    assert "confirmation_source" not in prov      # agent did NOT self-attest
    assert prov.get("anchor_status") is None
    assert prov["anchor_project_id"] == "github.com/acme/app"
    assert prov["extractor"] == "onboard-repo@test"
    assert prov["anchor_detail"] == {"version": "^18.2.0"}
    assert entry.get("domain") == "repo-fact"


def test_accept_onboard_candidate_verifies_and_stamps_anchor(eng):
    golden = Path(__file__).resolve().parent / "fixtures" / "onboard_repo_golden"
    eng.create_onboard_candidate(
        "This project depends on `react` (^18.2.0).",
        anchor_ref="dep:react",
        anchor_detail={"version": "^18.2.0"},
        anchor_project_id="github.com/acme/app",
        extractor="onboard-repo@test",
    )
    item_id = _by_anchor(eng, "dep:react")["id"]

    eng.accept_onboard_candidate(item_id, project_root=str(golden))

    accepted = _by_anchor(eng, "dep:react")
    assert accepted["tier"] == "verified"          # owner granted trust
    prov = accepted["provenance"]
    assert prov["confirmation_source"] == "anchor"  # owner-confirmed via anchor
    assert prov["anchor_status"] == "valid"         # react is in the golden fixture
    assert prov.get("last_validated_at")


def test_accept_unknown_anchor_when_no_project_root(eng):
    eng.create_onboard_candidate(
        "This project depends on `react` (^18.2.0).",
        anchor_ref="dep:react",
        anchor_detail={"version": "^18.2.0"},
        anchor_project_id="github.com/acme/app",
        extractor="onboard-repo@test",
    )
    item_id = _by_anchor(eng, "dep:react")["id"]

    eng.accept_onboard_candidate(item_id)  # no project_root -> cannot verify anchor

    accepted = _by_anchor(eng, "dep:react")
    assert accepted["tier"] == "verified"
    assert accepted["provenance"]["anchor_status"] == "unknown"


def test_create_onboard_candidates_from_enumeration(eng):
    anchors = [
        {"kind": "dep", "ref": "react", "detail": {"version": "^18.2.0"},
         "source": "package.json", "anchor_ref": "dep:react"},
        {"kind": "file", "ref": "README.md", "detail": {"hash": "abc123"},
         "source": "file", "anchor_ref": "file:README.md"},
        {"kind": "unsupported", "ref": "pyproject.toml", "source": "pyproject.toml"},
    ]
    summary = eng.create_onboard_candidates(anchors, repo_id="github.com/acme/app")

    assert summary["created"] == 2      # unsupported skipped
    assert summary["skipped"] == 1

    dep = _by_anchor(eng, "dep:react")
    assert dep is not None
    assert dep["tier"] == "staging"
    assert "react" in dep["summary"] and "^18.2.0" in dep["summary"]
    assert dep["provenance"]["anchor_project_id"] == "github.com/acme/app"

    file_fact = _by_anchor(eng, "file:README.md")
    assert file_fact is not None
    assert "README.md" in file_fact["summary"]
